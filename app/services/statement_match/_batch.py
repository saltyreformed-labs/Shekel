"""Apply everything the owner reviewed in ONE pass, and say what each item did.

Plan step ``bank_import:X-f6a-3c-2``, finding **N-306**.  The review screen
offers two acts -- accept a proposed match, record a bank line as a purchase --
and until this step each one was its own request through its own money door.
Measured on the developer's own 2026-08-16 statement against a production
clone: **124 proposals and 91 recordable lines, 215 round trips**, each paying
``candidates_for`` at **3.593 s** before it wrote a row.  **12.88 minutes of
derivation to work one statement**, which is why the corrections do not get
made.  The same 215 acts through this door, end to end and applied for real:
**5.80 s**, against **762.7 s** for a control that re-derives per act -- and
the two produce byte-identical outcomes on all 215 items and identical balances
on six sampled days.

**It is not "accept everything"** (ruling **R-FP**).  Nothing here decides
anything: every item in a batch is one the owner ticked, carrying the same ids
the screen showed, and every one of them goes through the same door, the same
refusals and the same settle verbs a single-item request always did.  What the
batch removes is the round trip, not the review.

**The failure policy is the developer's ruling of 2026-08-19**: a refused item
leaves nothing behind and the rest still land, each refusal quoted with its own
sentence.  It is not a hypothetical bound -- 5 of the developer's own 124
proposals refuse today and will keep refusing, all of the same CLASS: a settled
credit-card payback whose recorded figure has drifted from the card entries it
repays, so any later entry edit on its envelope is refused (finding **N-323**,
two paybacks, `$59.68` of drift).  A batch that failed whole would lose 119
good corrections to it.

**How each item is isolated: a SAVEPOINT.**  ``db.session.begin_nested()``
around each act, released when it lands and rolled back when it refuses.  The
REQUEST is still the transaction and the route still owns the commit, so a
batch that dies part-way commits nothing at all -- the savepoint bounds a
DESIGNED refusal, never a failure.

**A broken invariant is not a refusal and is not caught.**  The
``RuntimeError`` a walk raises on one (``cash_ledger``'s readers, for
instance) is a fact about the account rather than about the item, so it fails
the whole request loud (``CLAUDE.md`` rule 4).  *It was the stored ledger's
``PostingError``, from a door's ledger reconcile, until plan step plan#2914
stopped the doors reconciling that ledger.*

**Order: every match, then every create, then every income, then every skip,
each in the order it was submitted** -- which is the order the screen renders
them, so the receipt reads down the page.  *This named two arms until plan
step ``bank_import:X-gj-4b``, and the income arm had been missing from it
since ``X-gf-1``* -- checkable: ``a4db019f`` adds the income loop and does not
touch this paragraph.  A sentence enumerating the loops is a claim about the code
below it, so it is re-counted off them rather than appended to.

**SKIP runs LAST, and that is the ruled precedence rather than an arbitrary
tail.**  A line can carry at most one verb (ruling **bank_import:R-HP**), and
:func:`~._skipping.skip_line` refuses a line an accepted match already answers
-- reading the database, so it sees what THIS pass has already flushed.  Put
the skips first and the same collision refuses the MATCH instead, through
:func:`~._resolve.load_lines`.  The developer's 2026-08-19 ruling on the
create-versus-match collision is that the act explaining money the records
already hold WINS, and this is that ruling one verb over: a skip records that
nothing explains the line, so it may not beat a match that does.  No browser
can submit the pair -- :func:`~app.schemas.validation.statement_reconcile
.reconcile_payload` keys one verb per line -- so this decides only what a
crafted body gets.  It is a real decision rather than an arbitrary one: two ticked items can
collide, because an envelope a match names may also be the destination a
recorded line was aimed at, and one of them has to be refused.  The matches
run first, so the match settles the envelope on a covering movement, and a
settled row holding a payment takes no purchase (ruling **R-BAL78**): the
creation is refused by the destination re-ask
(:func:`~._destinations.current_destinations`), which asks the envelope as
the match left it (``test_batch``'s
``test_a_creation_cannot_target_an_envelope_a_match_claimed`` grades it).
Measured on the developer's own statement, 4 envelopes are
both named by a proposal and offered as a destination, and **15 of the 91
recordable lines aim at one**.  The developer ruled 2026-08-19 that the
PROPOSAL wins: it explains money the records already hold against a line the
bank showed, where the recorded line can be re-aimed at another envelope on
the next pass.

**Each item FLUSHES before the next is validated**, and that is what makes one
shared derivation safe rather than merely fast: the destination re-ask above
reads the rows as the database holds them, and so does every act's read of
what is already matched (:func:`~._candidates.matched_subjects`).

**Every LINE lock FIRST, in ONE order, before any arm runs** (plan step
``bank_import:X-gi-5``, finding **N-471**): four arms calling a door per item
took a pass's line locks in SUBMISSION order, and two presses naming the same
lines in different arms crossed.  :func:`~._resolve.lock_lines` carries the
argument, what it closes and what it leaves ``balance:X-bn``.

**A parent and its own child are NOT the only pair through which one item can
move a figure another item names, and saying they were was measured FALSE on
2026-08-19.**  Settling a matched purchase runs
``entry_service.update_entry``, which re-derives the envelope's CC Payback and
writes its ``estimated_amount`` -- a SIBLING rather than a child, invisible to
the guard that refused an envelope named beside its own purchase.  What
actually keeps a pass honest is that :func:`~._valuation.repriced` re-prices
every named row per act, and, since plan step ``bank_import:X-f6d-3``, that an
item whose row has moved since the screen described it is REFUSED rather than
written (finding **N-336**).  This paragraph asserted the refuted reason until
an adversarial review found it 2026-08-23; that guard's own docstring had
already been corrected, and the guard itself was deleted as unreachable at
plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding **CC-386**, ruling
**R-CC144**).

**What it hands back lives in** :mod:`._outcome` -- :class:`~._outcome
.BatchOutcome` with its two item values, and the :class:`~._outcome.Tally`
the four arms and :func:`_run` here write and :func:`apply_reviewed` freezes
last.  They left this file at plan step ``bank_import:X-gt`` (ruling
**bank_import:R-BI7**, finding **BI-491**), a pure move made with it at 998
of pylint's 1000 lines.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db

from ._accept import accept_match
from ._bars import MerchantAnswers
from ._create import MintedEnvelopes, create_purchase_from_line
from ._creations import IncomeCreation, PurchaseCreation
from ._income import record_income_from_line
from ._outcome import AppliedItem, BatchOutcome, RefusedItem, Tally
from ._receipt_sentences import (
    created_summary,
    income_summary,
    match_summary,
    skip_summary,
)
from ._resolve import lock_lines
from ._scope import ReviewScope
from ._skipping import SkipRequest, skip_line
from ._submission import MatchSubmission


class Consent(enum.Enum):
    """WHO agreed to the acts in one batch (ruling **R-GH**).

    Plan step ``bank_import:X-ge``.  **Consent splits by ACT CLASS and a
    standing rule is consent**, so a pass through this door is one of exactly
    two things and never a mixture: a person read a screen and ticked, or a
    rule the owner stated earlier fired on lines an import had just recorded.

    **It is an enum rather than the boolean the column holds**, and the reason
    is that the two are not the same fact at the same grain.
    ``budget.statement_matches.applied_by_rule`` records what performed ONE
    act (ruling **R-GT**); this records what assembled a WHOLE pass, and it is
    the thing the pass's own refusal is keyed on
    (:meth:`ReviewedBatch.__post_init__`).  Deriving the column from it in one
    place -- :attr:`applied_by_rule` -- is what keeps a batch from being
    described one way and recorded another.
    """

    TICKED = "ticked"
    STANDING_RULE = "standing_rule"

    @property
    def applied_by_rule(self) -> bool:
        """Return what this consent writes to ``applied_by_rule``.

        The ONE mapping from a pass's consent to the column ruling **R-GT**
        stores per act, so a door cannot record a rule's work as a tick.
        """
        return self is Consent.STANDING_RULE


@dataclass(frozen=True)
class ReviewedBatch:
    """What the owner ticked, in the order the screen showed it.

    Ids and the state each row was REVIEWED in, exactly as
    :class:`~._submission.MatchSubmission` and
    :class:`~._creations.PurchaseCreation` are: every figure and every day this
    door WRITES is re-derived from the rows the ids name, inside the same
    transaction, so a stale page cannot commit a number the database no longer
    holds -- and since plan step ``bank_import:X-f6d-3`` an item whose row has
    MOVED since the screen described it is refused rather than written
    (finding **N-336**), which is the other half of the same sentence.

    **It names no OWNER and no ACCOUNT either**: whose pass this is, is the
    :class:`~._scope.ReviewScope`'s, stated once.  A batch carrying its own
    pair beside a scope carrying another was a second answer nothing
    reconciled.

    Attributes:
        matches: The proposals ticked, plus the hand-built group where one was
            submitted -- they are the same act and reach the same door, so they
            are one list rather than two.
        creations: The bank lines the owner named a destination for.
        incomes: The bank lines of money COMING IN the owner ticked to record
            (ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``).  **Its own
            list beside *creations*, because they reach different doors and
            write different shapes**: a purchase is filed against a container
            the submission names, and an income row is filed against nothing
            and names only its line.  One list discriminated by the sign of a
            figure the submission does not carry would have to read the
            database to know which door an item meant.
        skips: The bank lines the owner decided are explained by NOTHING
            (ruling **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``).
            **Its own list beside the three above, because it reaches a door
            that writes a different TABLE and no money**:
            ``budget.statement_line_skips`` holds no figure, so a skip records
            a decision rather than a movement.  Its submission is one id and
            nothing else, as :class:`~._creations.IncomeCreation` is.
        consent: Who agreed to these acts (:class:`Consent`, ruling **R-GH**).
            **Required, with no default**, for the reason
            :func:`~._accept.record_match` gives its own keyword-only flag: the
            two values are *the owner agreed to this* and *the app did it on
            their behalf*, and a default would let a door claim the first by
            omission -- which is consent laundered rather than recorded.
    """

    matches: "tuple[MatchSubmission, ...]"
    creations: "tuple[PurchaseCreation, ...]"
    incomes: "tuple[IncomeCreation, ...]"
    skips: "tuple[SkipRequest, ...]"
    consent: Consent

    def __post_init__(self) -> None:
        """Refuse a rule pass carrying a MATCH.

        **It refused an INCOME too until plan step ``bank_import:X-gj-2a``**
        (ruling **R-HT(a)**); the comment below the match arm records why that
        sentence went false and why this one did not.

        **Ruling R-GH's boundary, made unrepresentable rather than maintained**
        (plan step ``bank_import:X-ge``).  A rule is consent for CREATING a row
        from a new bank swipe; every act that modifies a row the owner made by
        hand -- re-date, re-price, settle, group-match -- keeps its tick, and
        those are exactly the acts :func:`~._accept.accept_match` performs.
        That door hardcodes ``applied_by_rule=False`` and says no rule reaches
        it; this is the same sentence one tier up, where a caller could
        otherwise assemble the batch that contradicts it.

        A TICKED batch carrying no items at all is legal and ordinary -- it is
        what an untouched form posts -- so nothing here counts.

        **A rule pass may not carry an INCOME either** (ruling **bank_import:R-GW**,
        plan step ``bank_import:X-gf-1``), and that is a second sentence rather
        than a widening of the first: a merchant rule says where that
        merchant's SPENDING goes, so there is no answer it could hold that
        means *record this deposit*.  The door itself already refuses -- it is
        called with ``applied_by_rule=False`` as a literal -- and this is the
        same fact at the tier where a caller could assemble the batch that
        contradicts it, exactly as the match arm is.

        **A rule pass may not carry a SKIP either** (plan step
        ``bank_import:X-gj-4b``), and this arm is about the STORE rather than
        about the act class -- which is why it is a third sentence and not a
        widening of the first.  Ruling **R-GH** would permit it on its own
        terms: a skip creates a decision and modifies no row the owner made by
        hand.  What refuses it is that ``budget.statement_line_skips`` carries
        no ``applied_by_rule`` column, so nothing could record that a rule
        performed the act -- and three surfaces then state something false.
        The Skipped tab's Undo says *the record that YOU decided this bank
        line is explained by nothing*; :func:`~._filing.rule_filed_acts`
        selects on ``StatementMatch.applied_by_rule``, so a skip reaches no
        receipt item and no one-click undo, which is exactly what rulings
        **R-GH** and **R-GG** promise; and
        :attr:`~._filing.RuleFiling.filed_total` sums
        :attr:`~._outcome.AppliedItem.amount` over what landed, under the caption *what
        the bank moved on the lines a rule filed*, so a skipped line's figure
        would be reported as money a rule filed when a skip files nothing.

        **It is also true that no ANSWER means skip today**: ruling
        **bank_import:R-JH** holds that *never a purchase* states no
        disposition, and :class:`~._rules.RuleAnswer`'s five members contain no
        other candidate.  That is the reason this refusal is a CHECKLIST rather
        than a boundary -- the step that mints a sixth answer lifts it, exactly
        as ``bank_import:X-gj-2a`` lifted the income arm, and the design that
        makes it lift-able is ``docs/design/statement_disposition_model.md``
        (ruling **bank_import:R-JY**), where authorship is one column over all
        four verbs.

        Raises:
            ValueError: When a :attr:`Consent.STANDING_RULE` batch names a
                MATCH or a SKIP.  **A programming error rather than a designed
                refusal**, so it is not a ``ValidationError``: no wire value
                reaches this field, the route states it as a literal, and there
                is no sentence to write for an owner who cannot have caused it.
        """
        if self.consent is not Consent.STANDING_RULE:
            return
        if self.matches:
            raise ValueError(
                "A standing rule may create a row from a new bank swipe and "
                "may not modify a row the owner made by hand (R-GH), so a "
                "rule-consented batch cannot carry a match."
            )
        if self.skips:
            raise ValueError(
                "budget.statement_line_skips has no applied_by_rule column "
                "recording that a rule performed the act (R-GT), so a "
                "rule-consented batch "
                "cannot carry a skip: the Skipped tab would render the app's "
                "decision as the owner's own, the import receipt could "
                "neither itemise nor undo it, and RuleFiling.filed_total "
                "would report its line as money a rule filed."
            )
        # **A rule-consented batch MAY carry an income since plan step
        # ``bank_import:X-gj-2a``**, where this refused one.  The refusal read
        # *a merchant rule says where that merchant's SPENDING goes (R-GI), so
        # no rule can mean "record this deposit" (bank_import:R-GW)* -- true
        # while the answer set had four members, and ruling **R-HT(a)** added a
        # fifth that says what a DEPOSIT from a signature IS.  It is the same
        # act class R-GH consents to: it CREATES a row from a new bank line and
        # modifies nothing the owner made by hand.
        #
        # **The MATCH refusal above is untouched and is the whole boundary
        # now.** R-HT(b)'s group rule names a ROW SET, which modifies rows the
        # owner made, so it applies only on their OK -- and that is exactly the
        # act that reaches ``accept_match``.  So the one arm this may never
        # grow is the one still refused.

    @property
    def item_count(self) -> int:
        """Return how many acts this batch asks for.

        **Every kind, and the count moves with the field block above it.**  A
        count naming three of the four would under-report every pass carrying
        a skip, which is the shape :func:`~app.routes.accounts._statement_doors
        .submitted_item_count` records having shipped once.
        """
        return (
            len(self.matches) + len(self.creations) + len(self.incomes)
            + len(self.skips)
        )

    @property
    def line_ids(self) -> "frozenset[int]":
        """Return every bank line this batch names, across all of its arms.

        **What :func:`~._resolve.lock_lines` locks before any arm runs**
        (plan step ``bank_import:X-gi-5``), and it moves with the field block
        above for :attr:`item_count`'s reason: a set naming three of the four
        arms leaves the fourth's lines locked in submission order -- finding
        **N-471** itself.  ``test_lock_order`` grades it on the mechanism.
        """
        return frozenset(
            line_id
            for submission in self.matches for line_id in submission.line_ids
        ) | frozenset(
            item.line_id
            for item in self.creations + self.incomes + self.skips
        )


def _run(tally: Tally, line_ids: "tuple[int, ...]", act) -> object:
    """Run one act inside its own SAVEPOINT and record what happened.

    **The savepoint is what makes the ruled failure policy true rather than
    reassuring.**  A refused act may already have staged rows -- ``_create``
    creates a purchase before the match that names it is validated, and a
    settle verb can refuse mid-way through a group -- so "a refused item leaves
    nothing behind" needs the partial work undone without touching the items
    that landed before it.

    **``ValidationError`` and ``NotFoundError`` are caught, and nothing else.**
    Both are this project's DESIGNED refusals -- a sentence written for the
    person who submitted the form -- and they are SIBLINGS rather than one
    deriving from the other (``app/exceptions.py``), which is why naming only
    the first left a hole.  ``entry_service.update_entry`` and ``create_entry``
    raise ``NotFoundError`` for a row that has gone, so a row hard-deleted
    between this pass's derivation and this item's write took the whole request
    down as a 500 -- where the same event is a designed stale-page refusal
    everywhere else on this screen.  Found by adversarial financial review
    2026-08-19.

    Anything else propagates and fails the whole request, which is the right
    answer for a broken invariant (a fact about the account, not about this
    item) and for a database error.

    Args:
        tally: The running receipt.
        line_ids: The bank lines this act names, for the receipt.
        act: The service call, taking no arguments.

    Returns:
        Whatever *act* returned, or ``None`` when it was refused.
    """
    savepoint = db.session.begin_nested()
    try:
        result = act()
        # **Inside this item's savepoint, and that is the point.**  Autoflush
        # would otherwise emit THIS item's INSERTs while the NEXT item's first
        # query runs -- inside the next item's savepoint -- so refusing that
        # one would roll back work this one had landed.  An earlier comment
        # here claimed the flush keeps an ``IntegrityError`` inside the
        # savepoint, which it does not: only a designed refusal is caught, so
        # an integrity error fails the whole request either way.  Named by
        # adversarial test-quality review 2026-08-19.
        #
        # It is also what makes the next item's refusals see this one: they
        # read what is already matched, and re-ask whether a destination
        # still takes a purchase as the rows now stand.
        db.session.flush()
    except (ValidationError, NotFoundError) as exc:
        savepoint.rollback()
        tally.refused.append(RefusedItem(line_ids=line_ids, reason=str(exc)))
        return None
    savepoint.commit()
    return result


# **FOUR ARMS, ONE PER ACT CLASS, and the split is what the fourth forced.**
# ``apply_reviewed`` ran all four loops inline until plan step
# ``bank_import:X-gj-4b``, at which point it was over pylint's locals limit
# of 15 and its branch limit of 12 -- both of which `.pylintrc` leaves at the
# default, so both are checkable.  Neither figure it exceeded them BY is
# stated: those are measurements on an intermediate no commit holds.  The
# honest answer to a function over the limit is to DECOMPOSE it rather than to
# widen the limit
# (``docs/coding-standards.md``), and the seam is the one the value already
# draws: :class:`ReviewedBatch` carries one list per act class, each reaching
# its own door and writing its own shape, so one arm per list restates no
# partition.  Each is private and takes the shared accumulator, because what
# leaves this module is the frozen :class:`~._outcome.BatchOutcome` built from it.


def _apply_matches(tally, batch: ReviewedBatch, scope: ReviewScope) -> None:
    """Accept every ticked match, in the order it was submitted.

    Args:
        tally: The running receipt.
        batch: What the owner ticked.
        scope: The pass's derived offer set.
    """
    for submission in batch.matches:
        line_ids = tuple(sorted(submission.line_ids))
        accepted = _run(
            tally, line_ids, lambda s=submission: accept_match(s, scope),
        )
        if accepted is None:
            continue
        tally.settled += accepted.settled_count
        tally.corrected += accepted.corrected_count
        tally.redated += accepted.redated_count
        tally.repriced += accepted.repriced_count
        if accepted.residual is not None:
            tally.residuals += 1
            tally.residual_total += accepted.residual
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=match_summary(accepted),
            # Already the BANK's own signed figure over the lines this act
            # names, which is this field's stated convention.
            amount=accepted.amount,
        ))


def _apply_creations(tally, batch: ReviewedBatch, scope: ReviewScope, minted, answers) -> None:
    """File every ticked line as a purchase, in the order it was submitted.

    Args:
        tally: The running receipt.
        batch: What the owner ticked.
        scope: The pass's derived offer set.
        minted: The per-request registry, so a sweep mints one definition
            per answer and places one row of it per pay period rather than
            one per line (:class:`~._container.MintedEnvelopes`).
        answers: What the owner has said about this account's merchants, read
            ONCE for the whole door.
    """
    for creation in batch.creations:
        line_ids = (creation.line_id,)
        recorded = _run(
            tally, line_ids,
            lambda c=creation: create_purchase_from_line(
                c, scope, minted, answers,
                # **The PASS's consent, not the item's** (ruling **R-GT**,
                # plan step ``bank_import:X-ge``).  Which rule fired is
                # derivable from the matched line; that a rule fired at all is
                # not, and it is a fact about how this whole batch was
                # assembled rather than about any one line in it.
                applied_by_rule=batch.consent.applied_by_rule,
            ),
        )
        if recorded is None:
            continue
        # **AFTER the act returned**, never inside the door that creates: an
        # item refused by ``create_entry`` rolls its whole SAVEPOINT back, and
        # a registry written one line above that refusal hands the NEXT line an
        # id the rollback has already taken.  Measured -- the sweep died on
        # ``NoneType`` -- which is why the remembering lives out here.
        # EVERY recorded purchase, not only one that created its envelope:
        # a first firing that converged on a placed envelope of its name has
        # a definition the rest of the press must learn (``remember``'s
        # docstring carries what the ``envelope_created`` gate cost).
        minted.remember(creation, recorded)
        # **Counted by DIRECTION, off the field the door stated** (ruling
        # **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``).  One
        # count reported both, and the receipt's caption for it -- *recorded as
        # a purchase your records did not have, dated the day your bank took
        # it* -- is false of a refund in both halves.  The direction is
        # ``CreatedPurchase.records_a_refund``, which the create door resolved
        # through ``_rules.is_inflow`` while it held the line, so nothing here
        # tests a sign.
        if recorded.records_a_refund:
            tally.refunded += 1
        else:
            tally.recorded += 1
        tally.envelopes += 1 if recorded.envelope_created else 0
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=created_summary(recorded),
            # **NEGATED, onto the bank's convention.**
            # ``CreatedPurchase.amount`` is the purchase's own figure and the
            # bank states the same movement with the opposite sign, so the
            # receipt negates it back.  **Sign-general by construction**: the
            # negation is what the bank's convention is, not an assumption that
            # the purchase is positive, so a refund recorded at ``-28.29``
            # reports the ``+28.29`` the statement shows (ruling
            # **bank_import:R-II**).  Refunds DO reach this door since plan
            # step ``bank_import:X-gj-2b-2``, so the general case is the
            # ordinary one rather than a future one.
            amount=-recorded.amount,
            # **The merchant the door filed FOR, onto the receipt** (plan step
            # ``bank_import:X-gx``): the standing-rule offer is derived from
            # the applied items, so it names the merchant the locked row held
            # and not the one the page's derivation hydrated before the lock.
            merchant=recorded.merchant,
        ))


def _apply_incomes(tally, batch: ReviewedBatch, scope: ReviewScope, view) -> None:
    """Record every ticked deposit, in the order it was submitted.

    Args:
        tally: The running receipt.
        batch: What the owner ticked.
        scope: The pass's derived offer set.
        view: The rule view the creation arm already read.
    """
    for income in batch.incomes:
        line_ids = (income.line_id,)
        deposited = _run(
            tally, line_ids,
            lambda i=income: record_income_from_line(
                i, scope, view,
                # **The PASS's consent, not the item's** (ruling **R-GT**),
                # which is the same threading the creation arm above does: the
                # act records whether a RULE performed it, and only the batch
                # knows that.
                applied_by_rule=batch.consent.applied_by_rule,
            ),
        )
        if deposited is None:
            continue
        tally.deposited += 1
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=income_summary(deposited),
            # **Already the BANK's own direction.**  An income row's cash
            # effect is POSITIVE and so is the line it was built from
            # (``_income._load_line`` refuses anything else by name), so unlike
            # the purchase arm above there is no sign to flip -- and flipping
            # one here would report a deposit as a withdrawal.
            amount=deposited.amount,
        ))


def _apply_skips(tally, batch: ReviewedBatch, scope: ReviewScope) -> None:
    """Record every ticked line as explained by nothing.

    **Called LAST**, and :mod:`._batch`'s header carries the reason: a line a
    match in this same pass has just answered must lose the SKIP rather than
    the match, which is the developer's 2026-08-19 precedence ruling one verb
    over.

    Args:
        tally: The running receipt.
        batch: What the owner ticked.
        scope: The pass's derived offer set.
    """
    for skip in batch.skips:
        line_ids = (skip.line_id,)
        recorded_skip = _run(
            tally, line_ids,
            lambda s=skip: skip_line(
                s.line_id, scope.owner_id, scope.account_id,
            ),
        )
        if recorded_skip is None:
            continue
        # **A repeat wrote NOTHING, so it counts as nothing** -- see
        # :attr:`~._outcome.BatchOutcome.skipped_count`.  It is still an APPLIED item: the
        # door did not refuse it, the decision the owner asked for stands, and
        # its own sentence says which of the two happened.
        if recorded_skip.was_already_skipped:
            tally.already_skipped += 1
        else:
            tally.skipped += 1
        tally.applied.append(AppliedItem(
            line_ids=line_ids, summary=skip_summary(recorded_skip),
            # **Already the BANK's own signed figure**, straight off the line
            # the door held, so there is no sign to flip: a skip has no
            # app-side figure of its own to convert FROM, which is the whole
            # difference between this arm and the creation arm above.
            amount=recorded_skip.line.amount,
        ))


def apply_reviewed(batch: ReviewedBatch, scope: ReviewScope) -> BatchOutcome:
    """Apply every act the owner ticked, and report each one.

    Does NOT commit -- the route owns the session boundary, so a request that
    fails outside a designed refusal writes nothing at all.

    **It does not LOG the pass either**, and that is the same boundary: an
    event asserting "a reviewed pass was applied" must not sit in the log when
    the transaction that would have applied it failed, so the route emits it
    after its commit -- exactly as ``statements.record_statement``'s own
    business event is emitted by its route rather than by its service.

    Args:
        batch: What the owner ticked.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **The ROUTE builds it, exactly as only a route builds a
            ``BalanceContext``**, and this door takes it like every door
            beneath it.  A first draft built its own, which is the same shape
            one tier up as the per-act derivation this step exists to remove:
            the route needs that scope too -- to render the refusal a rolled-
            back pass leaves behind -- and a door that derives privately forces
            the caller to derive again.

    Returns:
        The :class:`~._outcome.BatchOutcome`.
    """
    # **EVERY ROW LOCK FIRST, in one order** (plan step ``bank_import:X-gi-5``,
    # finding **N-471**; the argument is :func:`~._resolve.lock_lines`'s).
    # Before any arm, because an item that ran ahead of it would lock its
    # line in submission order, which is the cycle this removes.
    lock_lines(scope.account_id, batch.line_ids)
    tally = Tally(applied=[], refused=[])
    # **One registry per REQUEST**, which is what makes a sweep mint one
    # envelope per answer per pay period rather than one per line (finding
    # **N-327**).  Built HERE rather than inside the create door for the reason
    # the scope is built by the route: a door that made its own would be a door
    # that converges with nothing, one line at a time, which is the defect.
    minted = MintedEnvelopes.none_yet()
    # **One derivation per DOOR, and that is the precise claim** -- an
    # adversarial review 2026-08-24 measured a first version of this comment
    # saying "per REQUEST", which is false: this route re-renders after the
    # write, and ``_leftovers`` builds its own bars for the screen, so
    # ``merchant_rules`` is read once here and once there.  That is the
    # shape ``state_rules`` already argues for and ships -- a door reads for
    # ITSELF across a write boundary, because sharing one read across it would
    # rest on an enumeration of what cannot have changed.
    #
    # What this DOES remove is the per-ACT read: ruling **R-GJ** bars a
    # merchant from becoming a purchase, nothing inside a batch can restate a
    # rule, and a door that re-read the table per act would ask it 90 times
    # for the developer's own statement.  Built HERE rather than on the
    # :class:`~._scope.ReviewScope` because the rule-stating route derives
    # its scope BEFORE its write, so a scope-carried answer would be the one
    # that pass had just replaced.  It is built unconditionally, including for
    # a pass carrying no creations at all: two indexed reads, measured at
    # 0.14 ms over the developer's 378 lines, against a value whose only
    # cheaper form would be one that means *nothing is barred*.
    #
    # **The RULE VIEW is read here too, and the two SHARE that read** (plan
    # step ``bank_import:X-gj-2a``).  ``CreationBars.build`` already accepts
    # the rules rather than re-reading them, which is how ``_leftovers``
    # builds both from one read; doing the same here means this door still
    # asks ``merchant_rules`` exactly ONCE, as the paragraph above claims,
    # while the income arm below gains the answer it needs.
    answers = MerchantAnswers.build(scope.owner_id, scope.account_id)
    view = answers.view

    _apply_matches(tally, batch, scope)
    _apply_creations(tally, batch, scope, minted, answers)
    _apply_incomes(tally, batch, scope, view)
    # **LAST, and the module header says why**: a line a match in this
    # same pass has just answered must lose the skip rather than the
    # match, which is the 2026-08-19 precedence ruling one verb over.
    _apply_skips(tally, batch, scope)

    outcome = BatchOutcome(
        applied=tuple(tally.applied),
        refused=tuple(tally.refused),
        settled_count=tally.settled,
        corrected_count=tally.corrected,
        redated_count=tally.redated,
        repriced_count=tally.repriced,
        recorded_count=tally.recorded,
        refunded_count=tally.refunded,
        envelopes_created=tally.envelopes,
        deposited_count=tally.deposited,
        residual_count=tally.residuals,
        residual_total=tally.residual_total,
        skipped_count=tally.skipped,
        already_skipped_count=tally.already_skipped,
    )
    return outcome
