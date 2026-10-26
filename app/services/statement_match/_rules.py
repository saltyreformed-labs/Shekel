"""What a merchant RULE is, and what this account has one for.

Plan step ``bank_import:X-f6a-3d``, decomposed at ``bank_import:X-gd-2``.  The
review screen's leftover list is 91 lines on the developer's own statement and
**21 merchants**, so the work it asks for is 91 decisions where the real
question was asked 21 times.  This module holds the ANSWER to those 21 and what
it can still resolve against; :mod:`._stating` is the door that records one and
:mod:`._placement` is what an answer comes to for a single line.

**The split is by CONSEQUENCE, not by size** (the module passed 1,000 lines and
that is what forced the question, not what answered it).  Everything here
READS: a value type, four indexed queries and one derivation over them, none of
which can mutate a row.  Everything in :mod:`._stating` writes.  A reader that
needed only to know what the owner said used to import a module that also held
the write door, its refusals and its receipt vocabulary.

**Nothing here writes money, and nothing here CAN.**  A rule is read to
SUGGEST; the only thing that records a purchase is an explicit destination
submitted for one specific line (:mod:`._create`).  That separation is the
developer's ruling of 2026-08-19 and it is what keeps ruling **R-FZ**'s *the
destination select IS the tick* whole: the select still opens on *leave this
line alone*, the rule is rendered beside it, and one sweep control -- the
same shape the per-class proposal sweep already has -- is what turns
suggestions into ticks.  A default that arrives pointing at money is exactly
what R-FZ removed, and a remembered default would be one.

**A rule resolves against the pass's own offer set, so it can never widen
it.**  :func:`~._placement.placements_for` picks from
:func:`~._destinations.destinations_for` -- narrowed by what this pass has
already matched -- rather than querying for a row of its own, which is the
property :func:`~._resolve.resolve_rows` rests on: a destination the screen may
not offer is one a rule cannot reach either.  Every way a rule can fail to
resolve is REPORTED rather than substituted for, because substituting is how a
suggestion becomes a guess.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate




class RuleAnswer(enum.Enum):
    """Which of the five answers a stated rule gives (**R-GI**, **R-HT(a)**).

    **A closed set of five, and the model's CHECK is what closes it**
    (``ck_merchant_rules_one_answer``).  It is derived from which columns a row
    carries plus one boolean rather than stored beside them, because a stored
    discriminator is a second statement of what the columns already say --
    which is :meth:`of`'s own argument, and ruling **R-GS**'s.

    The absence of a rule is NOT a member: it is ``None`` where a
    :class:`StandingRule` would be, and the screen says *you have not said*
    rather than *you said nothing*.  Collapsing it into :attr:`ALWAYS_ASK` is
    the tempting move and it loses the one fact that separates them -- *I have
    not decided* against *I have decided to have no standing answer* -- which
    is exactly what an import must not confuse, because one of them is a
    question still owed to the owner and the other is a question already
    answered.

    **Three of the five SUGGEST and two do not, and the split is not
    cosmetic.**  :attr:`TEMPLATE` and :attr:`NEW_ENVELOPE` name a container for
    money LEAVING, so :func:`~._placement.placements_for` resolves them against
    one outflow line.  :attr:`INCOME_CATEGORY` names what money ARRIVING is, so
    :func:`~._placement.inflow_placement_for` resolves it against one inflow
    line.  :attr:`NEVER` names none and BARS -- it is a
    :class:`~._bars.CreationBar`, resolved before a destination is looked for
    (ruling **R-GJ**) -- and :attr:`ALWAYS_ASK` names none and bars nothing,
    which makes it the only answer whose effect on money is exactly the effect
    of having said nothing.

    **A CONTAINER ANSWER RESOLVES IN BOTH DIRECTIONS, and that is plan step
    ``bank_import:X-gj-2b-2``'s correction to this paragraph.**  It used to read
    *the three that suggest are told apart by DIRECTION as well as by
    container, and no answer resolves in both directions* -- true only while
    ``ck_transaction_entries_positive_amount`` forbade a negative purchase.
    :attr:`TEMPLATE` and :attr:`NEW_ENVELOPE` name a container for that
    merchant's money in EITHER direction: money leaving is a purchase, and money
    arriving is a REFUND -- a negative purchase back into the same container
    (ruling **R-HT(a)**).  Only :attr:`INCOME_CATEGORY` is one-directional, and
    it is so because an outflow under it names no container to spend from.

    **Which act an answer implies is therefore :func:`pipeline_for`'s
    question and no longer the line's SIGN**; see that function for the whole
    table.
    """

    TEMPLATE = "template"
    NEW_ENVELOPE = "new_envelope"
    INCOME_CATEGORY = "income_category"
    NEVER = "never"
    ALWAYS_ASK = "always_ask"

    @classmethod
    def of(cls, row: MerchantRule) -> "RuleAnswer":
        """Return which answer *row* holds, read off its own columns.

        **The inverse of :func:`_columns_of`, and the pair is graded as a round
        trip** over every member rather than by two independent cases: a fifth
        answer added to one side and not the other is the failure this shape
        invites, and the round trip is what catches it.

        Args:
            row: The stored rule.

        Returns:
            Its :class:`RuleAnswer`.  TOTAL with no fall-through arm, because
            ``ck_merchant_rules_one_answer`` has already made the five
            combinations exclusive -- and, in particular, pins
            ``never_a_purchase`` false on all three answers that name
            something, so the order these are asked in cannot change the
            answer.  **That is what the migration adding the fifth had to widen
            every PRE-EXISTING arm for** (plan step ``bank_import:X-gj-2a``): a
            CHECK naming only the new column's own arm would still admit a row
            carrying a template AND an income category, and this reading takes
            the container first, so such a row would file SPENDING under an
            answer stated about deposits.
        """
        if row.template_id is not None:
            return cls.TEMPLATE
        if row.envelope_name is not None:
            return cls.NEW_ENVELOPE
        if row.income_category_id is not None:
            return cls.INCOME_CATEGORY
        return cls.NEVER if row.never_a_purchase else cls.ALWAYS_ASK


class LinePipeline(enum.Enum):
    """Which ACT an unexplained bank line is a candidate for.

    Plan step ``bank_import:X-gj-2b-2``.  The app has exactly two, and they are
    separate the whole way down -- separate placement, separate card, separate
    collection on :class:`~._batch.ReviewBatch`, separate apply loop, separate
    door, separate receipt:

    * :attr:`PURCHASE` -- filed into a budget container that reserves for it
      (``CreatableLine`` -> ``batch.creations`` -> ``_create.create_purchase_from_line``);
    * :attr:`INCOME` -- recorded under a category, reserving nothing
      (``RecordableInflow`` -> ``batch.incomes`` -> ``_income.record_income_from_line``).

    **They are deliberately NOT unified behind a common type.**  Nothing
    consumes them polymorphically -- every consumer immediately needs to know
    which it has, because it renders a different control and calls a different
    door -- so a base class over them would be an abstraction with no
    polymorphic caller, which is CLAUDE.md rule 13's speculative shape.  What
    IS shared is the DECISION, and that is what this enum plus
    :func:`pipeline_for` make one statement of.
    """

    PURCHASE = "purchase"
    INCOME = "income"


#: The answers that name a SPENDING CONTAINER, which is what makes a line a
#: purchase in either direction.  A frozenset rather than a tuple so the
#: membership test below states set membership, and named once so
#: :func:`pipeline_for` and its exhaustiveness test cannot drift.
CONTAINER_ANSWERS = frozenset({RuleAnswer.TEMPLATE, RuleAnswer.NEW_ENVELOPE})


def is_inflow(amount: Decimal) -> bool:
    """Return whether a bank line's *amount* is money ARRIVING.

    **THE ONE STATEMENT OF THE BANK'S SIGN CONVENTION** (plan step
    ``bank_import:X-gj-2b``).  ``bank_statement_lines.amount`` and
    :attr:`~._offers.BankLine.amount` are both signed POSITIVE INTO the
    account, and until this step every reader that needed the direction spelled
    the comparison itself.  **The count went UP, not down**, which is why this
    exists: re-measured over both trees, ``app/services/statement_match/`` held
    **4** such comparisons at ``04980186`` (the two door refusals and the two
    list partitions) and **6** at ``1bfeff07``, because ``pipeline_for``'s first
    signature took a BOOL and three call sites spelled
    ``is_inflow=line.amount > 0`` to feed it -- under a commit titled *the SIGN
    stops routing*.  :class:`~._panel.AddAct` already carried the standing rule
    against exactly that, and says the two spellings *would not even agree*.
    Six spellings of one convention are six places to write it backwards, and
    what it decides is which way real money moved.

    **The partition is TOTAL, and the schema is why.**
    ``ck_bank_statement_lines_amount_real_nonzero`` declares ``amount <> 0``,
    so ``not is_inflow(amount)`` IS *money leaving* rather than *money leaving
    or nothing at all*. That is what lets every caller take two arms instead of
    three, and it is a fact about the table rather than about any reader here
    -- which is why there is no branch for zero and no case asserting one: a
    guard no mutation can reach, graded by a test that grades nothing, is what
    :meth:`~._bars.CreationBars.bar_for` records having had to delete.  The
    CONSTRAINT is what carries the guarantee and it is graded directly, by
    ``test_statement_import_schema.TestALineMustMoveMoney`` and by
    ``test_income.test_the_reviewed_pass.TestWhichLinesGetAnActAndWhichSTILLDoNot.
    test_a_ZERO_line_cannot_EXIST``.

    (An earlier draft of this paragraph named a class
    ``TestTheSchemaIsWhatMakesTheTwoDoorsTotal``, which **has never existed** in
    this repository -- it was carried over from
    :func:`~._income._load_line`'s own docstring, where plan step
    ``bank_import:X-gf-1`` wrote it and nothing checked it.  A citation nobody
    opens is what this project files findings about.)

    Args:
        amount: The line's own figure, on the bank's convention. A
            :class:`~decimal.Decimal`, never a float -- the comparison is
            exact and the caller's column is ``NUMERIC(12, 2)``.

    Returns:
        Whether the bank moved money INTO the account on this line.
    """
    return amount > 0


def pipeline_for(
    *, amount: Decimal, answer: "RuleAnswer | None",
) -> LinePipeline:
    """Return which act an unexplained line is a candidate for.

    **THE ONE PLACE THE TWO PIPELINES ARE TOLD APART** (plan step
    ``bank_import:X-gj-2b-2``), asked by the SCREEN
    (:func:`~._leftovers.leftovers`) and by the WRITE DOOR
    (:func:`~._create._load_line`) so the screen cannot render a control the
    door would refuse -- the invariant this package states at every other door
    it has.

    **The discriminant is the ANSWER and the direction only bounds it, which is
    the correction this step makes.**  The partition used to be the line's
    SIGN: ``_creatable_lines`` took ``amount < 0`` and ``_recordable_inflows``
    took ``amount > 0``, so what a line BECAME was decided before the owner's
    rule was consulted at all.  That was exact only while a purchase had to be
    positive.  A merchant credit is money ARRIVING that must become a PURCHASE
    -- a negative one, back in the container the owner named -- so the sign
    cannot decide it and never could have; it was standing in for the answer.

    **Total over the whole domain, which is (2 directions x 5 answers + no
    rule) = 12 cells:**

    ==================  ===================  ==========================
    answer              money OUT            money IN
    ==================  ===================  ==========================
    ``TEMPLATE``        PURCHASE             PURCHASE (a refund)
    ``NEW_ENVELOPE``    PURCHASE             PURCHASE (a refund)
    ``INCOME_CATEGORY`` PURCHASE, no rule    INCOME
    ``NEVER``           PURCHASE, then bar   INCOME, no rule
    ``ALWAYS_ASK``      PURCHASE, no rule    INCOME, no rule
    ``None``            PURCHASE, no rule    INCOME, no rule
    ==================  ===================  ==========================

    **TEN of those twelve cells RESTATE what the code already did, and TWO
    change** -- the money-IN row of ``TEMPLATE`` and of ``NEW_ENVELOPE``, both
    of which routed to INCOME at ``04980186`` and route to PURCHASE here.
    *This paragraph said ELEVEN and ONE until plan step
    ``bank_import:X-gj-2b-3``*, which collapsed a two-answer class to one cell:
    at ``04980186`` ``_creatable_lines`` selected on ``line.amount < 0``, so
    EVERY inflow reached ``_recordable_inflows`` whatever its answer, and both
    container answers changed together.  It is the third generation of one
    counting error -- the first draft claimed two cells "had never been
    decided", and adversarial review measured THAT false: at
    ``04980186`` ``_creatable_lines`` took EVERY outflow and ``placements_for``
    fell through to ``None`` for ``INCOME_CATEGORY``, and ``_recordable_inflows``
    took EVERY inflow including ``NEVER`` with ``inflow_placement_for``
    answering ``None``. Both behaviours were determinate and both are unchanged
    here.  They were never STATED in one place, which is what this table fixes;
    that is a clarification and not a decision, and the claim carried a
    developer attribution it did not deserve.

    **The one real change is (container answer, inflow)**: from an income
    candidate carrying an unresolved reason, to a PURCHASE -- the refund.

    Two cells are worth reading twice even though neither moved, because the
    table is where their reasons now live:

    * **money OUT under ``INCOME_CATEGORY``** enters the purchase pipeline with
      NO suggestion.  Naming a container the owner never gave would be the
      guess ruling **R-HX** refused, so the owner picks by hand.  **A reviewer
      argues this should be the mirror of the refund** -- a withdrawal from an
      income merchant is a clawback or a fee, a contra-INCOME -- and that is an
      open question for the developer rather than a settled cell.
    * **money IN under ``NEVER``** enters the income pipeline with NO
      suggestion, and is not barred HERE.  Whether ruling **R-GJ**'s bar should
      reach an inflow at all is a separate open question, argued at
      :func:`~._bars.reject_barred_line`; this cell only records that routing
      does not decide it.

    **An outflow is always a PURCHASE candidate**, whatever the answer -- the
    rule only decides whether anything is SUGGESTED inside that pipeline, and
    ``NEVER`` is then answered by :class:`~._bars.CreationBars` further down,
    which is where ruling **R-GJ** put it.

    Args:
        amount: The line's own figure, on the bank's convention (positive INTO
            the account).  **The AMOUNT rather than a caller-computed bool**,
            which is this step's own adversarial review's correction: the bool
            made every caller spell ``line.amount > 0`` for itself, so a change
            whose commit title read *the SIGN stops routing* had in fact taken
            the executable sign tests in this package UP rather than down; the
            re-measured counts are in :func:`is_inflow`, which is the one place
            that census is stated.  The
            direction is asked HERE, through :func:`is_inflow`, so the
            convention is written once.  Not the LINE, because the two callers
            hold different types -- the screen a :class:`~._offers.BankLine`
            and the write doors a ``BankStatementLine`` -- and neither should
            have to build the other's view model to ask.
        answer: What the owner has said about this line's merchant, or ``None``
            where they have said nothing or the source names no merchant.
            ``None`` and :attr:`RuleAnswer.ALWAYS_ASK` answer the same here and
            deliberately: they differ in what the SCREEN says (*you have not
            said* against *you said to ask*), never in which act the line is a
            candidate for.

    Returns:
        The :class:`LinePipeline` this line belongs to.
    """
    if not is_inflow(amount):
        return LinePipeline.PURCHASE
    if answer in CONTAINER_ANSWERS:
        return LinePipeline.PURCHASE
    return LinePipeline.INCOME


@dataclass(frozen=True)
class StandingRule:
    """One stated answer to "where does this merchant's spending go?".

    Attributes:
        merchant_id: The :class:`~app.models.merchant.Merchant` this answers
            for, which is the key (plan step ``bank_import:X-gd-1``).  It was
            the bank's own string, matched to a line's own copy of it by
            equality; the merchant is a row now, so the key is its id.
        merchant: What that merchant is CALLED, carried beside its id because
            every sentence this module writes names it and the query that read
            the answer read the name with it.  The same read-model join
            :attr:`~._offers.BankLine.merchant` is.
        answer: Which of the four it is (:meth:`RuleAnswer.of`).
        template_id: The recurring definition to file into, for
            :attr:`RuleAnswer.TEMPLATE`; ``None`` otherwise.
        envelope_name: What to call the envelope to create, for
            :attr:`RuleAnswer.NEW_ENVELOPE`; ``None`` otherwise.
        category_id: The category to create it under, likewise.
        income_category_id: What a DEPOSIT from this merchant is, for
            :attr:`RuleAnswer.INCOME_CATEGORY`; ``None`` otherwise.  **A
            separate field from** :attr:`category_id` **for the reason the
            column is separate from its own**: the two answer different
            questions -- where this merchant's spending goes, and what its
            deposits are -- and one field holding either would need every
            reader to know which answer it was looking at before it could read
            it.
    """

    merchant_id: int
    merchant: str
    answer: RuleAnswer
    template_id: "int | None" = None
    envelope_name: "str | None" = None
    category_id: "int | None" = None
    income_category_id: "int | None" = None

    @property
    def is_new_envelope(self) -> bool:
        """Return whether this answer creates an envelope.

        **The screen's own question, answered here rather than as a truth test
        on :attr:`envelope_name` in a Jinja condition** -- which would be the
        arm INFERRED from a column rather than read from the one field that
        states it, the shape that made the existing-envelope destination
        unreachable from a browser at plan step X-f6a-3b.
        """
        return self.answer is RuleAnswer.NEW_ENVELOPE

    @property
    def is_income_category(self) -> bool:
        """Return whether this answer says what a DEPOSIT from here is.

        Asked here for the reason :attr:`is_new_envelope` is: the screen's own
        question, answered on the value that states it rather than inferred in
        a Jinja condition from whether an id happens to be set.
        """
        return self.answer is RuleAnswer.INCOME_CATEGORY

    @property
    def is_never(self) -> bool:
        """Return whether this answer is *never a purchase*.

        Asked here for the reason :attr:`is_new_envelope` is, and for one more:
        a template comparing ``answer.value == 'never'`` would be a string
        comparison standing in for an identity, which is the substitution this
        project's reference rule exists to refuse.
        """
        return self.answer is RuleAnswer.NEVER

    @property
    def is_always_ask(self) -> bool:
        """Return whether this answer is *ask me every time* (ruling **R-GS**).

        Asked here for the reason the two above are.  **It is the answer whose
        rendering is easiest to get wrong**, because it looks like the absence
        of one: the screen must show it SELECTED, on a control whose unselected
        state submits *I have not said* -- the same shape that once let a stale
        template silently withdraw a rule
        (:attr:`RuleView.stale_templates`).
        """
        return self.answer is RuleAnswer.ALWAYS_ASK

    @classmethod
    def of(cls, row: MerchantRule, merchant: str) -> "StandingRule":
        """Return *row* as the value every reader here shares.

        Args:
            row: The stored rule.
            merchant: What the merchant it answers for is called, read in the
                same statement as the row itself
                (:func:`rules_for`) -- so no reader here has to go back for
                a name to put in a sentence.

        Returns:
            Its :class:`StandingRule`, carrying the answer
            :meth:`RuleAnswer.of` reads off the columns the model's CHECK has
            already made exclusive.
        """
        return cls(
            merchant_id=row.merchant_id,
            merchant=merchant,
            answer=RuleAnswer.of(row),
            template_id=row.template_id,
            envelope_name=row.envelope_name,
            category_id=row.category_id,
            income_category_id=row.income_category_id,
        )


def _named_templates(
    template_ids: "set[int]", account_id: int,
) -> "dict[int, str]":
    """Return ``{id: name}`` for *template_ids* on *account_id*, in one query.

    **The account clause closes finding N-353** (`X-gd-1`'s adversarial
    security review, 2026-08-25).  This was the one query on user data in this
    package with no scope clause: it selected by id alone, and the NAME it
    returns is RENDERED, on the control that says where a merchant's spending
    goes.  Nothing reachable leaked -- the ids arrive from
    ``merchant_rules.template_id`` on rows already scoped to this owner and
    account, and ``fk_merchant_rules_template_account`` holds each to its
    account -- but that is safety by DERIVATION over an open set of future
    callers, and this project has measured its own only-way arguments wrong
    often enough to stop accepting them.  The caller already holds the account,
    so the safety is local now and costs nothing.

    Args:
        template_ids: The ids wanted.  Empty issues no query -- ``IN ()`` is a
            statement with no rows to find.
        account_id: The account whose templates may be named.

    Returns:
        The names by id.  An id belonging to another account is ABSENT rather
        than refused, which is what :meth:`RuleView.label_for` is total over:
        a name it cannot find is a phrase, not a raise on a read path.
    """
    if not template_ids:
        return {}
    rows = (
        db.session.query(TransactionTemplate.id, TransactionTemplate.name)
        .filter(
            TransactionTemplate.id.in_(template_ids),
            TransactionTemplate.account_id == account_id,
        )
        .all()
    )
    return dict(rows)


def _named_categories(
    category_ids: "set[int]", owner_id: int,
) -> "dict[int, str]":
    """Return ``{id: display name}`` for *category_ids* of *owner_id*.

    :func:`_named_templates`' twin, for the other arm a stored answer can name
    (plan step ``bank_import:X-gd-2``).  It exists for the same reason and is
    scoped for the same reason: the NAME it returns is rendered, so the safety
    is local rather than inherited from where the ids came from.

    Args:
        category_ids: The ids wanted.  Empty issues no query.
        owner_id: The user whose categories may be named.

    Returns:
        The display names by id.  Built with
        :attr:`~app.models.category.Category.display_name` rather than a
        column, because that is what every other category control on this
        screen shows and a bare ``item_name`` would read as a different
        category.

    """
    if not category_ids:
        return {}
    rows = (
        db.session.query(Category)
        .filter(Category.id.in_(category_ids), Category.user_id == owner_id)
        .all()
    )
    return {row.id: row.display_name for row in rows}


@dataclass(frozen=True)
class RuleView:
    """What the owner has SAID, and what it can still resolve against.

    **One derivation at one instant**, which is the same argument
    :class:`~._scope.ReviewScope` makes one tier down and is not a parameter
    list dressed up: a rule names a template and a category, and whether
    either is still reachable is read from the same moment the rule is.
    Read separately, a category archived between two of those reads would be
    resolvable by one caller and refused by the next inside one render.

    Attributes:
        rules: What the owner has answered, by merchant id
            (:func:`rules_for`).
        template_names: What to call each recurring definition a rule on this
            account may name (:func:`offerable_templates`) -- the option list,
            and the sentence an unresolvable placement explains itself with.
        active_categories: The categories a new envelope may still be created
            under, and the ones an income answer may still file a deposit into
            (:func:`active_category_names`).  **One mapping for both**,
            because it is one question -- has the owner retired this category
            -- and an answer naming a retired one is reported rather than acted
            on, in either direction.  It carries the NAMES too, so the card
            that states an income answer prints the label from the same read
            that resolved the answer.
        stale_templates: What to call a template a stored rule NAMES that is
            no longer offerable, by id.  **A rendered control must be able to
            show the answer it holds, even a stale one**: without this the
            select had no option carrying the stored value, so it displayed --
            and submitted -- its first, which is *I have not said*, and the
            next Save silently WITHDREW a rule the owner never touched.  A
            template is deactivated or un-enveloped through
            ``routes/templates/crud.py``, so this is live user state rather
            than a hypothetical.  Found by adversarial financial review
            2026-08-19.  Usually empty, and then it costs no query at all.
        stale_categories: The same, for a CATEGORY a stored answer names that
            is no longer active, by id (plan step ``bank_import:X-gd-2``).
            **BOTH answer columns feed it since plan step
            ``bank_import:X-gj-2a``** -- the category a *new envelope* answer
            creates under, and the income category a deposit answer files under
            -- because what this dict answers is *what is this archived
            category called*, which is one question and has no direction.  One
            id can arrive from both answers on two different merchants, and a
            set is what makes that cost one lookup.  **The template arm had this and the
            category arm did not**, and the category arm's failure was worse:
            with no option carrying the stored value the select submitted
            ``""``, which reaches the door as a NEW ENVELOPE answer missing its
            category and is REFUSED -- so pressing Save to answer about one
            merchant printed "a new envelope needs both a name and a category"
            for another the owner never touched, on every pass, naming the
            wrong half.  Found by two adversarial reviews 2026-08-26, which
            also measured what made it reachable: teaching
            ``archive_helpers.category_has_usage`` about this table turned a
            permanent delete (which cascaded the rule away, leaving nothing to
            mis-render) into an ARCHIVE, which is exactly this state.
    """

    rules: "dict[int, StandingRule]"
    template_names: "dict[int, str]"
    active_categories: "dict[int, str]"
    stale_templates: "dict[int, str]"
    stale_categories: "dict[int, str]"
    # The offerable definitions that have NO rule -- a grid one-off's, a
    # bank-born envelope's -- whose rows a TEMPLATE answer PLACES where a
    # paycheck holds none (:attr:`~._placement.PlacementKind.PLACE`, ruling
    # **R-BAL24**, leaf 7b-3 of ``balance:X-bi-7b``).  Read off the same rows
    # ``template_names`` is, so the two cannot name different definitions.
    placeable_templates: "frozenset[int]"
    # For each placeable definition, the paychecks that already hold a LIVE
    # row of it -- any status, offerable or not -- so the PLACE arm is never
    # offered where R-BAL24's one row per paycheck already stands
    # (``one_off.holds_a_row_in`` is the same fact asked at the door).  One
    # read over those definitions' rows; found by 7b-3's adversarial reviews,
    # which placed onto a cancelled row and met the occurrence index.
    placed_periods: "dict[int, frozenset[int]]"

    @classmethod
    def build(cls, owner_id: int, account_id: int) -> "RuleView":
        """Derive the view for one pass over one account.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account being reviewed.

        Returns:
            The :class:`RuleView`.  Three small indexed reads, plus one each
            for a stored answer whose template has stopped being offerable or
            whose category has been archived; the review screen's cost is its
            3.6 s candidate derivation, not this.
        """
        rules = rules_for(owner_id, account_id)
        offerable_rows = _offerable_template_rows(account_id)
        offerable = {row.id: row.name for row in offerable_rows}
        active = active_category_names(owner_id)
        placeable = frozenset(
            row.id for row in offerable_rows if not row.recurs
        )
        return cls(
            rules=rules,
            template_names=offerable,
            active_categories=active,
            placeable_templates=placeable,
            placed_periods=_placed_periods(placeable),
            stale_templates=_named_templates(
                {
                    rule.template_id for rule in rules.values()
                    if rule.template_id is not None
                    and rule.template_id not in offerable
                },
                account_id,
            ),
            stale_categories=_named_categories(
                {
                    named for rule in rules.values()
                    for named in (rule.category_id, rule.income_category_id)
                    if named is not None and named not in active
                },
                owner_id,
            ),
        )

    def label_for(self, template_id: int) -> str:
        """Return what to call *template_id*, offerable or not.

        Args:
            template_id: The recurring definition a rule names.

        Returns:
            Its name.  TOTAL, because a caller asking is holding an id a rule
            already carries -- and a composite foreign key holds that id to
            this account -- so the only way it is unknown here is a row deleted
            between this view's two reads, where the honest answer is a phrase
            rather than a raise on a read path.
        """
        return (
            self.template_names.get(template_id)
            or self.stale_templates.get(template_id)
            or "a recurring envelope"
        )

    def category_label_for(self, category_id: int) -> str:
        """Return what to call *category_id* when it is no longer active.

        :meth:`label_for`'s twin, and TOTAL for the same reason: a caller
        asking is holding an id a stored rule carries, and
        ``fk_merchant_rules_category_owner`` holds that id to this owner -- so
        the only way it is unknown here is a row hard-deleted between this
        view's two reads, where the honest answer is a phrase rather than a
        raise on a read path.

        Args:
            category_id: The category a stored *new envelope* answer names.

        Returns:
            Its display name, or a phrase.  **Active first**, since plan step
            ``bank_import:X-gj-2a``: this answered only for an ARCHIVED
            category, which was every category a caller had reason to name
            while the only readers were the two unresolved-placement sentences.
            Ruling **R-HT(a)**'s card names a LIVE one, and a reader that fell
            through to *an archived category* for it would print that phrase on
            the sentence describing a working rule.
        """
        return (
            self.active_categories.get(category_id)
            or self.stale_categories.get(category_id)
            or "an archived category"
        )


def rules_for(
    owner_id: int, account_id: int,
) -> "dict[int, StandingRule]":
    """Return every rule this owner has stated for this account, by merchant.

    **The merchant's NAME is read in the same statement as its answer** (plan
    step ``bank_import:X-gd-1``).  Every sentence this module writes names the
    merchant and the screen prints it beside the control, so a second read for
    it would be a redundant producer call inside one request -- which this
    project treats as a DRY violation rather than a cost.

    Args:
        owner_id: The user the caller proved owns the account.
        account_id: The account being reviewed.

    Returns:
        ``{merchant_id: StandingRule}``.  One answer per merchant is
        structural (``uq_merchant_rules_account_merchant``), so the
        mapping cannot lose a row to a collision.
    """
    rows = (
        db.session.query(MerchantRule, Merchant.name)
        .join(
            Merchant,
            db.and_(
                Merchant.id == MerchantRule.merchant_id,
                Merchant.account_id == MerchantRule.account_id,
            ),
        )
        .filter(
            MerchantRule.user_id == owner_id,
            MerchantRule.account_id == account_id,
        )
        .all()
    )
    return {
        row.merchant_id: StandingRule.of(row, name) for row, name in rows
    }


def account_merchants(account_id: int) -> "dict[int, str]":
    """Return every merchant this account's statements have ever named.

    **It was a UNION of two derivations and it is now a table** (plan step
    ``bank_import:X-gd-1``).  ``statable_merchants`` computed *every merchant
    this account's recorded lines name* and unioned it with *every merchant
    already answered for*; the second half existed because deleting an import
    took a merchant's lines with it and would otherwise have made a stated
    answer unwithdrawable -- the section renders an answered merchant whichever
    half it came from, and a check reading only the first half would have
    refused that submission whole.  An ANSWERED
    :class:`~app.models.merchant.Merchant` row OUTLIVES its lines, so the union
    IS this table and the second half has nothing left to add.  The set is
    that union and no more because deleting an import removes the merchants
    its sightings alone name and no answer is about
    (``statement_import._reads.orphan_merchants_by_import``), the half that
    preserved nothing, and because no door mints a merchant it does not also
    write onto a sighting (``statement_import._merchants.resolve_merchants``).

    **What it is still FOR is the sentence, not the scope.**  A rule names a
    ``merchant_id`` held to this account by
    ``fk_merchant_rules_merchant_account``, so a merchant this account
    has never seen is unwritable rather than refused; this read is what lets
    :func:`_refuse_unknown_merchants` answer a stale page with a sentence
    instead of an ``IntegrityError``, and what gives every refusal here a name
    to print.

    Args:
        account_id: The account being reviewed.  ``merchants`` carries no
            ``user_id`` of its own -- it is account-scoped, exactly as
            ``bank_statement_lines`` is -- so the account IS the ownership
            statement here.

    Returns:
        ``{merchant_id: name}``.  **In no stated order**, because both readers
        index it and neither iterates: the order a screen shows merchants in is
        :func:`~._section.merchant_section`'s, which sorts by name over a
        NARROWER set and is graded there.  A promise of order here would be one
        no caller keeps and no case could catch breaking.
    """
    rows = (
        db.session.query(Merchant.id, Merchant.name)
        .filter(Merchant.account_id == account_id)
        .all()
    )
    return dict(rows)


def _offerable_template_rows(account_id: int) -> "list[TransactionTemplate]":
    """Return the envelope definitions a rule on this account may name, as rows.

    The one read behind :func:`offerable_templates` and
    :attr:`RuleView.placeable_templates`, so the names a rule control offers
    and the definitions a TEMPLATE answer may PLACE a row of come off one row
    set.  The rule rides on each row's joined load, so ``recurs`` costs no
    query per definition.

    Args:
        account_id: The account being reviewed.

    Returns:
        The rows, ordered by name.
    """
    return (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.account_id == account_id,
            TransactionTemplate.is_envelope.is_(True),
            TransactionTemplate.is_active.is_(True),
            # The reference rule: IDs for logic, strings for display.
            TransactionTemplate.transaction_type_id != ref_cache.txn_type_id(
                TxnTypeEnum.INCOME,
            ),
        )
        .order_by(TransactionTemplate.name)
        .all()
    )


def _placed_periods(placeable: "frozenset[int]") -> "dict[int, frozenset[int]]":
    """Return, per placeable definition, the paychecks holding a live row of it.

    Every live row, whatever its status: R-BAL24 gives a rule-less
    definition ONE row per paycheck, and a cancelled or fixed-figure-closed
    row is that paycheck's row though it is not offerable.  One query over
    the placeable definitions' rows, for the PLACE arm's guard.

    Args:
        placeable: :attr:`RuleView.placeable_templates`.

    Returns:
        ``{template_id: frozenset(pay_period_id)}``; a definition with no
        live row is absent.
    """
    if not placeable:
        return {}
    held: "dict[int, set[int]]" = {}
    for template_id, period_id in (
        db.session.query(Transaction.template_id, Transaction.pay_period_id)
        .filter(
            Transaction.template_id.in_(placeable),
            Transaction.is_deleted.is_(False),
        )
        .distinct()
    ):
        held.setdefault(template_id, set()).add(period_id)
    return {key: frozenset(value) for key, value in held.items()}


def offerable_templates(account_id: int) -> "dict[int, str]":
    """Return the envelope definitions a rule on this account may name.

    **A one-off's definition is among them since plan step ``balance:X-bi-7b``
    and BY RULING** (**R-BAL24** rejected a ``recurs`` filter here): an
    envelope the owner made at the grid carries a rule-less definition, and
    since the family's third leaf a TEMPLATE answer naming one PLACES its row
    in any paycheck that holds none
    (:func:`~._placement._template_placement`), which is what closed the
    UNRESOLVED-everywhere cost 10.10 of ``from_scratch_architecture.md``
    recorded for the interim.

    **Not every template, and the filter is the create door's own.**
    :func:`~._create.create_purchase_from_line` files a purchase through
    ``entry_service.create_entry``, which refuses a parent that does not track
    purchases -- so a rule naming a template that generates a plain budget
    line would be an answer whose every placement is refused.  The other two
    clauses mirror :func:`~._destinations.destinations_for`: a transfer's legs
    are the transfer service's, and money coming IN is not a purchase.

    Args:
        account_id: The account being reviewed.

    Returns:
        ``{template_id: name}``, which is what the rule control renders and
        what :func:`_template_placement` names in its refusals.
    """
    return {row.id: row.name for row in _offerable_template_rows(account_id)}


def active_category_names(owner_id: int) -> "dict[int, str]":
    """Return the categories a new envelope may still be created under.

    **The create door's own clause, asked where the SUGGESTION is made.**
    ``_create._owned_category`` refuses an archived category, because
    ``category_service.list_active_categories`` is what the picker renders --
    so a placement naming one would be a control whose submission can never
    succeed, which is the shape finding **N-325** was just closed for one
    field over.  A HARD-deleted category takes its rule with it
    (``fk_merchant_rules_category_owner`` cascades); archiving is a soft
    state that leaves the row pointing at something the door will refuse, so
    this is the live half.

    **It returns the NAMES as well as the ids since plan step
    ``bank_import:X-gj-2a``**, and the reason is that a second reader appeared
    rather than that names are nice to have: ruling **R-HT(a)**'s income answer
    is stated as a category, so the Reconcile card's sentence has to print what
    that category is CALLED -- and reading it back in a second query would be
    the redundant producer call this project treats as a DRY violation, with
    the sharper risk that the label a card prints could then come from a
    different instant than the answer it describes.  Membership is unchanged:
    every existing reader asks ``id in view.active_categories``, which a
    mapping answers exactly as a set did.

    Args:
        owner_id: The user whose categories may be reached.

    Returns:
        ``{category_id: display name}``, built with
        :attr:`~app.models.category.Category.display_name` for the reason
        :func:`_named_categories` builds its own that way: a bare ``item_name``
        reads as a different category from the one every other control shows.
    """
    rows = (
        db.session.query(Category)
        .filter(Category.user_id == owner_id, Category.is_active.is_(True))
        .all()
    )
    return {row.id: row.display_name for row in rows}
