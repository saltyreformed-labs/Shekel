"""
Shekel Budget App -- What the reconcile panel renders

The value types the reconcile step publishes across the services boundary:
one tickable purchase, the block it nests in, and the whole offer set.  They
live together because they are ONE shape -- the panel's list -- and because
every arm of the package contributes to them: the purchase arm fills
:attr:`OutstandingGroup.purchases` today, and plan steps X-f2-c2 and X-f2-c3
add the parent's own close tick, bills and transfer shadows to the same
blocks.  A type per arm would put the panel's shape in three places and make
the next arm's field a fourth.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Frozen dataclasses, no behaviour beyond the two
    derivations :class:`OutstandingSet` owns.
  - All monetary values are :class:`~decimal.Decimal`.
"""

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.match_withdrawal import MatchWithdrawal
from app.services.pay_calendar import DerivedPeriod
from app.services.reconcile_service._named import NamedLines, SharedMatch
from app.services.reconcile_service._rows import Statement


class OfferKind(enum.Enum):
    """What a block IS, for the two rules ruling **R-FC** puts on the order.

    The panel is ONE collection of blocks (R-FC rejected two held side by side),
    and two of that ruling's three presentational rules are about this value:
    the ordering key gains a kind term so like sits with like, and a section
    label is emitted where the kind changes.  The third -- a childless block
    prints inline -- is a property of the block's contents, not of its kind.

    **Each ARM TAGS its own offers with a kind; nothing DERIVES one.**  A first
    version of this classified a block by reading its contents -- purchases
    means envelope, correctable means bill -- and that was wrong twice.  An
    INCOME row is never purchase-tracked, so it came out ``BILL`` and rendered
    under a heading reading "Bills" three lines below a summary counting it as a
    deposit: production's `$1,958.87` FSA reimbursement, captioned against
    itself on the one screen read beside a paper statement.  And a transfer
    shadow is childless and correctable too, so it is INDISTINGUISHABLE from a
    bill by those proxies -- meaning plan step X-f2-c3 would have had to rewrite
    the derivation inside a money commit, which is the exact rewrite R-FC and
    **R-EY** exist to prevent.  An arm knows what it produced; a renderer
    guessing from shape does not.

    **The member's VALUE is the section heading the panel prints**, and
    :attr:`section_label` says so at the call site, because a bare ``.value``
    does not tell a reader the string is user-visible copy.

    **The definition ORDER is the section order**, and plan step X-f2-c3 added
    ``TRANSFER`` below as exactly that -- an addition, not an edit.  A rank map
    beside the class would be a second place to edit, and an omission there
    sorts a whole section to the wrong end rather than failing.

    **``SETTLEMENT`` is a section of WHERE a row is planned, not of what it
    is** (plan step ``credit_card:CC-5-4b``, ruling **R-CC44**, headed
    "Paid from this account" by ruling **R-CC111**): a row planned on ANOTHER
    account whose payment was recorded on this one and then reopened.  Its
    offers are bills, deposits and empty envelopes alike -- R-CC111 accepted
    the heading's mislabel of the rare deposit -- so what a tick DOES to one is
    not this tag's to say: closing an envelope is the offer's own
    :attr:`OutstandingTransaction.closes_envelope` (ruling **R-CC119**).

    It is a service-tier classification and not a ``ref`` table, so it carries
    no id and nothing compares it to a string: the assembler reads
    :attr:`rank`, the template reads a label the assembler already resolved.
    """

    ENVELOPE = "Envelopes"
    BILL = "Bills"
    DEPOSIT = "Deposits"
    TRANSFER = "Transfers"
    SETTLEMENT = "Paid from this account"

    @property
    def rank(self) -> int:
        """Return this kind's position in the panel's section order.

        Returns:
            The member's index in its own class's definition order, resolved
            through a map built once at import rather than by rebuilding the
            member list inside every sort comparison.
        """
        return _KIND_RANK[self]

    @property
    def section_label(self) -> str:
        """Return the heading the panel prints above this kind's first block.

        Returns:
            The user-visible section heading.
        """
        return self.value

    @property
    def section_note(self) -> "str | None":
        """Return the sentence printed UNDER this kind's section heading.

        Two kinds carry one, and each is a fact about the SECTION rather than
        about any row.  Ticking a transfer settles the matching row on the
        other account too, because a transfer's two legs and its parent always
        move together (``CLAUDE.md`` transfer invariant 3); a user who reads the
        panel as "this account's statement" would otherwise be surprised by a
        second account moving.  And every row under "Paid from this account"
        was recorded here and then reopened, and a tick records it here on the
        statement's day (ruling **R-CC111**'s note, plan step
        ``credit_card:CC-5-4b``).  Both are per SECTION and not per row because
        each row already names the other account ("Transfer to Fidelity Money
        Market Savings", "Groceries (Checking's plan, paid from this card)"),
        so a per-row caption would repeat what the label says.

        **A map beside the class, and here that is right where :attr:`rank`'s
        would be wrong.**  A missing rank silently sorts a section to the wrong
        end; a missing note prints no note, which is the correct rendering for
        the three kinds that have nothing extra to say.

        Returns:
            The user-visible caption, or ``None`` for a kind with no note.
        """
        return _KIND_NOTES.get(self)


#: Definition order, resolved once.  Derived from the class rather than written
#: out, so a new member cannot be added without a rank -- the failure mode a
#: hand-maintained map has is a silent mis-sort, not an error.
_KIND_RANK = {kind: index for index, kind in enumerate(OfferKind)}

#: The per-section captions, keyed by kind.  Absence means "no note"; see
#: :attr:`OfferKind.section_note` for why a map is safe here and is not for
#: :attr:`OfferKind.rank`.
_KIND_NOTES = {
    OfferKind.TRANSFER: (
        "Ticking a transfer settles both sides -- the matching row on the "
        "other account is settled at the same time."
    ),
    OfferKind.SETTLEMENT: (
        "Recorded on this account, then reopened. Ticking one records it "
        "here on your statement date."
    ),
}


class TickForm(enum.Enum):
    """HOW the panel posts a settle tick: its ids field, amount box and control id.

    Ruling **R-BAL145** (leaf ``balance:X-bi-6-4c-2``): a transfer's tick
    posts ``transfer_ids`` and ``transfer_amount-<transfer id>`` beside the
    bills' unchanged ``transaction_ids`` and ``settled_amount-<id>``, and the
    field names are spelled ONCE, on the offer the service publishes.  They
    had to become two because a tick stopped naming a row of ONE table: a
    transfer's is its transfer's id, which can equal a bill's, so one field
    carrying both could not say which a posted number meant.  This is that
    once: the offer derives its fields from its member
    (:attr:`OutstandingTransaction.tick_form`), the template prints them, and
    the route parses the posted form with the same members -- three readers,
    one spelling.

    **The member is a function of the offer's KEY and nothing else**
    (:meth:`of`): a row's key is its int id and a leg's the pair
    ``transfer_legs.cell_key`` gives it (ruling **R-BAL87**), so the key's
    shape already says which form a tick takes and no second field can
    disagree with it.

    **A form is per TABLE, not per list** (ruling **R-CC116**, plan step
    ``credit_card:CC-5-4b``): the "Paid from this account" list offers ROWS,
    so its tick posts :attr:`ROW`'s fields exactly as the same row's tick on
    its own account's list does.  The number means one thing in both lists,
    which is the property R-BAL145 made two fields to keep; the two row scopes
    partition on the row's account, so one posted id lands in at most one.

    Each value is ``(ids field, amount-box prefix, control-id prefix,
    shown-lines prefix)``; the third keeps a row's checkbox and a leg's apart
    in the page's DOM, where one id number can name both.  The fourth names
    the hidden field a tick posts the bank lines its caption printed under
    (plan step ``credit_card:CC-5-4a-5``, rulings **R-CC76** /
    **R-CC127**), and only a ROW has one: a leg holds no purchase to settle
    from, so once ruling **R-CC125** refuses a typed ``$0.00`` box no
    transfer tick the panel could caption takes a payment out of its
    matches, and a leg's tick names nothing and posts no field.
    """

    ROW = ("transaction_ids", "settled_amount-", "t", "shown_lines-")
    LEG = ("transfer_ids", "transfer_amount-", "l", None)

    @property
    def ids_field(self) -> str:
        """Return the form field a tick of this form posts its id under."""
        return self.value[0]

    @property
    def amount_prefix(self) -> str:
        """Return the prefix an amount box's field name carries before its id."""
        return self.value[1]

    @property
    def control_prefix(self) -> str:
        """Return the prefix a tick's checkbox id carries before its id."""
        return self.value[2]

    @property
    def shown_prefix(self) -> "str | None":
        """Return the prefix a tick's posted-lines field carries before its id.

        ``None`` for :attr:`LEG`, whose tick names no line (the class
        docstring).
        """
        return self.value[3]

    @classmethod
    def of(cls, key) -> "TickForm":
        """Return the form a tick keyed *key* takes.

        Args:
            key: An offer's :attr:`OutstandingTransaction.key`.

        Returns:
            :attr:`LEG` for a transfer leg's pair, :attr:`ROW` for a row id.
        """
        return cls.LEG if isinstance(key, tuple) else cls.ROW


@dataclass(frozen=True)
class Section:
    """The heading a block STARTS, when it starts one.

    Ruling **R-FC**'s third presentational rule, as a value: the panel prints a
    section heading where the kind changes, and one kind prints a sentence under
    it.  The two travel together because they appear in the same place and are
    resolved by the same pass, and because a block either starts a section or it
    does not -- two nullable fields could express "a note with no heading",
    which is not a state the panel has.

    Attributes:
        label: The heading, from :attr:`OfferKind.section_label`.
        note: The sentence under it, or ``None``.  The TRANSFER and
            SETTLEMENT sections have one (:attr:`OfferKind.section_note`).
    """

    label: str
    note: "str | None"


@dataclass(frozen=True)
class OutstandingTransaction:  # pylint: disable=too-many-instance-attributes
    """A settle tick offered -- an envelope's close, a bill, or a transfer's leg.

    Pylint: ``too-many-instance-attributes`` (10/7) -- the eighth is
    :attr:`closes_envelope` (ruling **R-CC119**), the ninth
    :attr:`withdraws` (plan step ``credit_card:CC-5-4a-5``, ruling
    **R-CC76**) and the tenth :attr:`shared` (leaf 5c-2c-1, ruling
    **R-CC135**), and these ten ARE one tick: what it names, when it lands,
    what it books and what the statement shows, whether a figure may be
    typed, which tally and section it counts in, and what the tick DOES --
    to a budget line, and to the bank lines a match explains, alone and with
    other ticks.  None of the last three can be read off the section once a
    section names where a row is planned (``OfferKind.SETTLEMENT``), and
    grouping any of the others into a sub-value would only re-spell the
    template's reads of them.

    The TRANSACTION arm's offer (plan step X-f2-c2, ruling **R-FA**), beside
    the purchase arm's :class:`OutstandingPurchase`, and the TRANSFER arm's
    too (plan step X-f2-c3).  Ticking a row settles it through
    ``transaction_service.settle_transaction`` -- the same verb the grid's
    Mark Paid calls -- stamping the statement's own day; ticking a leg settles
    its transfer through ``transfer_service.settle_transfer``.  A row planned
    on ANOTHER account whose kept payment is on this one (plan step
    ``credit_card:CC-5-4b``) is offered by the transaction arm's second scope
    and settles through the same verb.

    Attributes:
        key: WHICH thing is offered: a row's ``budget.transactions`` id --
            whichever of its two lists offers it -- or a
            transfer leg's ``(transfer id, account id)`` pair --
            ``transfer_legs.cell_key``, the ONE place a row and a leg are told
            apart (ruling **R-BAL87**).  It was ``transaction_id``, holding a
            transfer SHADOW's row id for a leg, until leaf
            ``balance:X-bi-6-4c-2`` offered the leg itself; a transfer id can
            equal a bill's id, so the two shapes are what keep them apart.
            What a tick POSTS is derived from it (:attr:`tick_form`,
            :attr:`tick_id`), and the arm's writer re-scopes that rather than
            trusting it, so publishing it grants nothing.
        attributed_on: The day the PROJECTION lands this row on -- its
            ``due_date``, falling back to its pay period's start and clamped
            into that period
            (:meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day`,
            over the span the owner's calendar DERIVES since pay-calendar plan
            step C4-a-2).  It is the day the panel captions the row with, and
            the same day the offer's own bound is measured against, so a row
            cannot be offered under a caption that disagrees with why it was
            offered.
        amount: **What ticking this row will BOOK**, resolved by
            ``transaction_service.settle_amount`` rather than read off a
            column.  The panel showing a figure the verb would not book is this
            arc's own root cause 1 applied to a screen, and it is why this
            field is not the row's contribution.
        is_correctable: Whether the panel renders :attr:`amount` as an editable
            input (ruling **R-FB**, sharpened by **R-FF**): true exactly when
            the settle verb takes its MANUAL branch, i.e. the row is not
            (envelope-tracked AND carrying entries).  Read off
            ``transaction_service.settles_from_entries`` so the panel cannot
            offer a box for a value the verb would ignore.
        cash_amount: What the STATEMENT shows, when that is LESS than
            :attr:`amount` -- and ``None`` whenever the two agree, which is
            every row but one shape.  Finding **N-226**: an envelope settles at
            ``sum(entries)`` over EVERY entry including the card ones, while
            what leaves checking is the debit half alone (the card half leaves
            later through its own CC Payback).  So a `$40` debit plus a `$60`
            card purchase is offered at `$100.00` on a screen captioned "tick
            everything your statement shows", against a statement showing
            `$40`.  The LEDGER was right either way -- the row's leg
            subtracted the credit sum -- and only the panel's figure was, so the
            fix is to print both rather than to change what a tick books:
            the RECORDED figure legitimately IS total spend.
        is_income: Whether this row is money ARRIVING.  The panel counts
            deposits separately from payments (ruling **R-FD**) because a
            deposit and a bill do not sum to anything a reader wants.
        kind: The section this offer puts its block in, TAGGED by the arm that
            produced it rather than derived downstream -- see
            :class:`OfferKind` for the two defects deriving it caused.  This
            arm sets ``DEPOSIT`` for income, ``ENVELOPE`` for a purchase-tracked
            row and ``BILL`` for the rest; the transfer arm sets ``TRANSFER``
            on every offer it makes (plan step X-f2-c3); the transaction arm's
            second scope sets ``SETTLEMENT`` (plan step
            ``credit_card:CC-5-4b``).
        closes_envelope: Whether ticking this offer CLOSES an envelope -- its
            row is purchase-tracked, so the tick ends a budget line rather
            than only recording a payment (see
            :attr:`OutstandingGroup.settle_closes_an_envelope`).  A fact of
            its own rather than read off :attr:`kind` since plan step
            ``credit_card:CC-5-4b`` (ruling **R-CC119**): a ``SETTLEMENT``
            offer's kind names where its row is PLANNED, and an empty
            envelope reopened there still closes when ticked.  Both row
            scopes read it off the ONE classification that tags a row
            ``ENVELOPE`` (``_transactions._offer_kind``), so on a row's own
            list it and :attr:`kind` cannot part; a transfer leg's is
            ``False``, since a leg is never purchase-tracked.
        withdraws: What ticking it would take out of its MATCHES -- the
            accepted acts left naming no app row and the bank lines they
            free (:class:`~app.services.match_withdrawal.MatchWithdrawal`),
            read through the act's own twin by the arm's reader (plan step
            ``credit_card:CC-5-4a-5``, ruling **R-CC76**: the panel names
            what a tick would free before the press, from the same read it
            acts on).  Read for one shape: a row on this account settling
            FROM its purchases while holding the payment a revert kept
            (``_transactions._settle_one``), which after rulings **R-CC125**
            and **R-CC126** is the one tick of a FRESH page the panel can
            caption; ``None`` for every other.  It holds the matches this
            tick empties BY ITSELF; one it empties only with other ticks is
            :attr:`shared`'s.  One tick it does not read can still take a
            payment off, uncaptioned (``_transactions._settle_one``: ledger
            row **BAL-596**).  The panel prints the caption and posts the
            lines it names (:attr:`shown_field`) only where it frees one.
        shared: The matches this tick withdraws only TOGETHER with other rows
            on the same list (leaf 5c-2c-1, ruling **R-CC135**,
            :class:`~._named.SharedMatch`) -- read in the same one pass as
            :attr:`withdraws`; empty for every tick but that same shape, and
            for every leg.

    :attr:`withdraws` and :attr:`shared` DEFAULT to a tick that frees
    nothing: the one arm that reads them adds them to the offer it built
    (``_transactions.outstanding_transactions``), and a tick an arm leaves
    at the default names no line, so a save of it that would free one is
    refused (ruling **R-CC127**: *"A button with no warning sends
    nothing"*).
    """

    key: "int | tuple[int, int]"
    attributed_on: date
    amount: Decimal
    cash_amount: "Decimal | None"
    is_correctable: bool
    is_income: bool
    kind: OfferKind
    closes_envelope: bool
    withdraws: MatchWithdrawal | None = None
    shared: "tuple[SharedMatch, ...]" = ()

    @property
    def tick_form(self) -> TickForm:
        """Return how this offer's tick is posted (ruling **R-BAL145**).

        Returns:
            :meth:`TickForm.of` over :attr:`key`.
        """
        return TickForm.of(self.key)

    @property
    def tick_id(self) -> int:
        """Return the id this offer's tick posts under :attr:`tick_form`.

        A row posts its own id; a leg posts its TRANSFER's, because the
        account half of its key is the statement's own and a form posting it
        back would be a second copy of a fact the route already holds.

        Returns:
            The row id, or the leg's transfer id.
        """
        if self.tick_form is TickForm.LEG:
            return self.key[0]
        return self.key

    @property
    def amount_field(self) -> str:
        """Return the form field name of this offer's amount box.

        Returns:
            :attr:`TickForm.amount_prefix` followed by :attr:`tick_id`.
        """
        return f"{self.tick_form.amount_prefix}{self.tick_id}"

    @property
    def control_id(self) -> str:
        """Return the suffix of this tick's checkbox DOM id.

        Returns:
            :attr:`TickForm.control_prefix` followed by :attr:`tick_id`.
        """
        return f"{self.tick_form.control_prefix}{self.tick_id}"

    @property
    def shown_field(self) -> "str | None":
        """Return the field name this tick posts its caption's bank lines under.

        Returns:
            :attr:`TickForm.shown_prefix` followed by :attr:`tick_id`, or
            ``None`` for a leg, whose tick names no line.
        """
        prefix = self.tick_form.shown_prefix
        return None if prefix is None else f"{prefix}{self.tick_id}"


@dataclass(frozen=True)
class OutstandingPurchase:
    """One purchase the account has not been seen to have paid for.

    A VALUE, not the ORM row: the panel renders four fields and the form posts
    one id, and publishing the row itself is what let the template reach
    ``entry.transaction.name`` per line -- one lazy SELECT per purchase, on a
    relationship the grouping has already loaded (see
    :func:`app.services.reconcile_service.outstanding_set`).

    Attributes:
        entry_id: The ``budget.transaction_entries`` id, and the value the
            tick posts back.  Re-scoped by
            :func:`~app.services.reconcile_service.record_settled_days` rather
            than trusted, so publishing it grants nothing.
        purchased_on: The day the purchase was MADE -- the budget clock, and
            what the row is captioned with.  Never the day it settled: an
            outstanding purchase has no settle day, which is the definition.
        description: The purchase's own description, as typed.
        amount: The purchase's amount, SIGNED.  **It said POSITIVE until plan
            step ``bank_import:X-gj-2b-3``**, which held only while
            ``ck_transaction_entries_positive_amount`` said ``amount > 0``.
            Ruling **bank_import:R-II** made a merchant credit a NEGATIVE
            purchase and one REACHES this set: the scope selects debit
            purchases with no recorded posting day, and a refund has none
            wherever the owner typed the negative on the edit door or cleared a
            posting day they had recorded.  It is not the bank-import door's
            refunds -- ``_born_purchase`` gives those the bank's own posting
            day at birth, so they are outside this set by construction.  The
            panel prints the figure through ``money()``, which signs it.
    """

    entry_id: int
    purchased_on: date
    description: str
    amount: Decimal


@dataclass(frozen=True)
class OutstandingGroup:
    """One source row, with the purchases against it that are still outstanding.

    **The grouping is ruling R-EW's shape**: a purchase nests under the thing it
    belongs to, so a grocery purchase and the grocery envelope read as one
    block.  Rejected there: grouping by act-type, which separates exactly those
    two, and a flat undifferentiated list, which is what shipped at S1-c and
    named each purchase's parent in a trailing fragment per line instead.

    **It is a GROUP and not an ``OutstandingEnvelope``, on two grounds.**
    Nothing in the purchase arm's scope asserts the parent is envelope-tracked
    -- the clauses ask for a projected, non-deleted parent on this account, and
    "it has purchases" is what makes it one in practice -- so the narrower name
    would claim a classification no clause checks.  And plan step X-f2-c2 puts
    BILLS and transfer shadows in this same set, neither of which is an
    envelope, so the narrow name would have to be renamed inside a leaf that
    moves money.

    Attributes:
        key: The parent's identity, and the key the grouping is built on: a
            row's ``budget.transactions`` id, or for a transfer's block its
            leg's ``(transfer id, account id)`` pair -- the same value as its
            :attr:`settle`'s :attr:`OutstandingTransaction.key`.  It was
            ``transaction_id`` until leaf ``balance:X-bi-6-4c-2``, holding a
            transfer SHADOW's row id for a transfer's block.
        name: The parent's name, for the block's heading -- for a transfer's
            block, its leg's label composed from the endpoints' CURRENT names
            (``transfer_legs.TransferLeg.name``) since leaf
            ``balance:X-bi-6-4c-2``, where it was the shadow's stored copy;
            for a row planned on ANOTHER account (plan step
            ``credit_card:CC-5-4b``), the row's name with that account's and
            where its money moved, "Groceries (Checking's plan, paid from this
            card)" (rulings **R-CC111** / **R-CC117**).
        period: The pay period the parent is budgeted in, as the owner's
            calendar DERIVES it, so the heading names WHICH one.  **Without it
            two blocks can carry the identical heading**: the recurrence engine
            materialises one row per template per period, so one envelope in
            two periods is two parents with one name, and both can hold
            outstanding purchases at one assertion.  The flat list was equally
            ambiguous per line; grouping PROMOTES that ambiguity to the heading,
            so the leaf that creates the heading is the leaf that has to resolve
            it.  **It was a ``period_start`` / ``period_end`` PAIR until
            pay-calendar plan step C4-a-2**, read out of the two columns that
            step's parent drops; one value is the same normalization the four
            paragraphs below apply to :attr:`kind` and :attr:`total`, since two
            date fields that must describe one paycheck are two fields a
            builder can fill from two.

            **It does NOT resolve the ambiguity completely, and it stops doing
            so entirely once `recurrence:R17`'s second leaf lands** -- that step
            makes the skip question ask about an OCCURRENCE rather than about a
            paycheck, so one template may legitimately materialise TWICE in ONE
            period (measured by that session on a production clone: at cadence
            31, eleven templates across twelve paychecks).  Two such rows share
            a name AND a period, so this heading separates them no better than
            the name alone.  Nothing is mis-BOOKED -- a tick posts
            :attr:`key`'s id, which differs -- and no such row exists on
            either database today.  What would separate them is the occurrence
            each answers, which is exactly the value that step adds; it is
            named here rather than fixed because the column does not exist on
            this branch and inventing a caption for it would be a guess.
        purchases: Its outstanding purchases, oldest first.  **May be EMPTY
            since plan step X-f2-c2**, and ruling **R-FC** is what that means
            for the screen: a childless block prints its name inline as one row
            rather than as a heading above a one-item list.  A bill is always
            childless; an envelope is childless when nothing it holds is
            outstanding but the row itself is.  A block with neither purchases
            nor a :attr:`settle` offer is never built.
        settle: The parent's OWN tick, or ``None`` when the row itself is not
            offerable -- the everyday mid-period case, where an envelope holds
            outstanding purchases but its own attribution day has not passed.
        section: The :class:`Section` this block STARTS, or ``None`` when it
            continues the previous one's.  Resolved by the assembler rather
            than in the template because it is a function of the ORDER, which
            the assembler owns; a template deriving it would re-read the
            previous element by index, and index arithmetic over a sorted list
            is how a section heading silently stops appearing.  Its TEXT is
            :class:`OfferKind`'s, so the template never compares a kind to a
            member to decide what to say.

    :attr:`kind` and :attr:`total` are PROPERTIES rather than fields, and that
    is the same normalization rule this arc applies to the database: both are
    functions of :attr:`purchases` and :attr:`settle`, so storing them beside
    their own inputs would be two values that can come to disagree inside one
    frozen object.
    """

    key: "int | tuple[int, int]"
    name: str
    period: DerivedPeriod
    purchases: "tuple[OutstandingPurchase, ...]"
    settle: "OutstandingTransaction | None"
    section: "Section | None"

    @property
    def kind(self) -> OfferKind:
        """Return which section this block belongs in (ruling **R-FC**).

        **Read off the offers wherever there is one to read.**  A block
        carrying its parent's own tick takes that offer's tag, which the arm
        that produced it set.  A block of purchases alone has no tag to read,
        so it falls back to ENVELOPE.

        **That fallback is a DERIVATION and it is not guaranteed sound**, which
        an earlier draft of this docstring claimed on a citation that does not
        hold: it said both entry write doors refuse a parent that is not
        purchase-tracked, and ``entry_service.update_entry`` has no such guard
        -- it validates the field names and ownership and nothing else.  Only
        ``create_entry`` checks ``tracks_purchases``, so the guarantee is
        "it carried entries WHEN THEY WERE WRITTEN", and a template's
        ``is_envelope`` is editable afterwards (the panel's own ``settle_row``
        macro says so).  A de-enveloped parent still holding entries therefore
        sections as an ENVELOPE here and as a ``BILL`` the moment its own close
        becomes offerable -- a mis-section, no wrong figure.  Left as a
        derivation rather than fixed here because the repair belongs with the
        arms: the purchase arm knows its parent and should TAG its blocks like
        the other two do.  **X-f2-c3's extraction (finding N-225) did NOT do
        it** -- ``_purchases`` is byte-unchanged by that leaf -- so this
        sentence named a completed step for work nobody had done, and the
        repair is still owed.

        **The settle's tag wins over the purchases**, and the case is real: an
        envelope with outstanding purchases AND an overdue close is one block,
        and its arm tagged it ``ENVELOPE`` -- the same answer.  Where they could
        differ is where the tag is the more specific fact.

        Returns:
            This block's :class:`OfferKind`.
        """
        if self.settle is not None:
            return self.settle.kind
        return OfferKind.ENVELOPE

    @property
    def settle_closes_an_envelope(self) -> bool:
        """Return whether ticking this block's own row CLOSES an envelope.

        The panel's copy needs the distinction because the two acts are not
        alike: settling a bill records that the bank took a figure, while
        closing an envelope ENDS a budget line -- it releases the unspent
        remainder and makes every later purchase against that row inert, since
        the entry reservation prices only Projected parents.  A row that reads
        as an observation when it is an act is how a user ticks away a period
        they were still spending in.

        Published as a boolean rather than letting the template compare
        :attr:`kind` to an enum member: reference-style values drive logic
        through ids and predicates here, never through a name a template
        matches on.

        **Read off the offer's own fact, not off the block's section**, since
        plan step ``credit_card:CC-5-4b`` (ruling **R-CC119**).  It asked
        ``kind is ENVELOPE`` until then, which was the same answer while every
        section named what its rows ARE; a "Paid from this account" block's
        section names where its row is PLANNED, and an empty envelope reopened
        there closes when ticked like any other -- so the panel prints "Close
        Groceries (Checking's plan, paid from this card)", the verb Checking's
        own list prints for the same row.

        Returns:
            True for a block carrying its parent's own tick when that tick
            closes an envelope (:attr:`OutstandingTransaction.closes_envelope`).
        """
        return self.settle is not None and self.settle.closes_envelope

    @property
    def total(self) -> Decimal:
        """Return the sum of :attr:`purchases`.

        **It is the PURCHASES' total and not the block's**, deliberately: an
        envelope settles at sum(entries), so adding :attr:`settle`'s amount to
        this would report `$200` against `$100` of money for a `$100` envelope
        holding two `$40` / `$60` purchases.  The panel prints this beside the
        heading of a block that HAS children and prints :attr:`settle`'s own
        amount on the settle row; the two are different units and are never
        summed.

        Returns:
            The sum, quantised by the source amounts rather than by any
            arithmetic here.  ``Decimal("0.00")`` for a childless block, which
            the panel does not render.
        """
        return sum(
            (purchase.amount for purchase in self.purchases), Decimal("0.00"),
        )


@dataclass(frozen=True)
class OutstandingSet:
    """What a statement of one civil day could still settle, grouped.

    **There are THREE tallies and deliberately no fourth that sums them**
    (ruling **R-FA**, extended by **R-FD**).  A single ``total`` double-counts
    the moment the close tick joins the purchases: an envelope settles at
    ``sum(entries)``, so counting its two `$40` / `$60` purchases AND its `$100`
    close reports `$200` against `$100` of money.  And a payment and a deposit
    do not sum to anything a reader wants.  Naming each tally for exactly what
    it holds is what stops a later leaf implementing a wrong definition
    literally -- which is what the field named ``total`` here was going to be.

    Attributes:
        groups: One block per parent with something outstanding, ordered by
            kind and then by the oldest offer the block carries (ruling
            **R-FC**).
        purchase_count: How many PURCHASES the set offers -- entries whose
            posting day has not been recorded.  Ticking one moves no money on
            its own; it releases that much of its envelope's reservation.
            **A refund is one of them** (ruling **bank_import:R-II**), which is
            that ruling's own vocabulary rather than a widening: a refund IS a
            negative purchase, and one the bank has not been seen to pay back
            is outstanding in exactly the sense this panel asks about.
        purchase_total: The sum of those purchases, SIGNED and NETTED.  Two
            offers of `$120.00` and `-$45.00` come to `$75.00`, and the panel's
            *N purchase(s) (X) still held back* reads on that net -- which is
            what the envelope's reservation is really holding, since `$120.00`
            is expected to leave and `$45.00` to arrive.  The count stays a
            count of ROWS: it says how many ticks the owner has to make.
        payment_count: How many EXPENSE rows the set offers -- an envelope's
            own close, or a bill, whichever list offers it (a row planned on
            another account is counted here too, plan step
            ``credit_card:CC-5-4b``).  Ticking one settles the row.
        payment_total: What those rows would book.
        deposit_count: How many INCOME rows the set offers -- money the
            projection is still waiting to arrive (ruling **R-FD**).
        deposit_total: What those rows would book.

    Counting lives here and not in the template because these are the figures
    the panel's copy pluralises on, and money-adjacent counting belongs on the
    services side of the boundary.
    """

    groups: "tuple[OutstandingGroup, ...]"
    purchase_count: int
    purchase_total: Decimal
    payment_count: int
    payment_total: Decimal
    deposit_count: int
    deposit_total: Decimal

    @classmethod
    def empty(cls) -> "OutstandingSet":
        """Return the set for an account with nothing to reconcile.

        The ROUTE's shape for an account carrying no assertion at all: there is
        no day for anything to be inside of, so the producer is never asked.
        It is a constructor here rather than a literal there because the zeros
        are MONEY, and the services boundary is where money is built (a route
        composing ``Decimal("0.00")`` is the shape ``outstanding_total`` had
        before plan step X-f2-c1, and it is how a caller ends up quantising).
        """
        zero = Decimal("0.00")
        return cls(
            groups=(),
            purchase_count=0, purchase_total=zero,
            payment_count=0, payment_total=zero,
            deposit_count=0, deposit_total=zero,
        )

    @property
    def is_empty(self) -> bool:
        """Return True when the account has nothing outstanding.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with its "nothing is being held back twice" copy
        rather than an empty form.

        **Read off the COUNTS, not off ``groups``**, and the difference is a
        wrong empty state rather than a style point.  This accessor was written
        at plan step X-f2-c1 against ``purchase_count`` alone, with its own
        docstring predicting that the next leaf would add a kind arriving from
        somewhere other than a purchase -- so extending the sum here is that
        prediction coming true, not a rewrite.  ``not self.groups`` remains
        wrong for a different reason each leaf; "nothing to tick" survives all
        of them.
        """
        return (
            self.purchase_count == 0
            and self.payment_count == 0
            and self.deposit_count == 0
        )


@dataclass(frozen=True)
class ReconcileSubmission:
    """One statement's worth of ticks, as the write union receives it.

    A parameter object rather than six arguments, for the reason
    ``docs/coding-standards.md`` gives and ``AccountSpec`` / ``TransferSpec``
    already follow: every field is read by more than one arm, and a six-argument
    signature is a signature whose call sites can transpose two ids of the same
    type without anything noticing.

    It is a VALUE and carries no behaviour: the route parses and owner-scopes
    it, :func:`~app.services.reconcile_service.record_reconciliation` runs the
    arms over it in the one order that works.

    **It carries the READ's own value rather than that value's parts, since
    pay-calendar plan step C4-a-2.**  It held an ``owner_id``, an
    ``account_id`` and an ``anchor`` -- which is exactly a
    :class:`~app.services.reconcile_service._rows.Statement`, and the write
    union then rebuilt one out of them on its first line.  Three fields
    reassembled into the value they came from is a second spelling of one
    thing, and it had already grown a second copy of that value's ``owner_id``
    derivation in this module.  Now the ROUTE builds one statement, the GET
    reads against it and the POST writes against it, and nothing can
    reconstruct a different one in between.

    Attributes:
        statement: The :class:`~app.services.reconcile_service.Statement` being
            reconciled -- the owner's pay calendar (whose rows these must be,
            and the spans they are dated against), the account whose balance was
            asserted, and the governing
            :class:`~app.services.cash_ledger.AnchorPoint`.  That anchor's id is
            what every ticked row records as having shown it (ruling **R-FL**)
            and its ``observed_on`` is the day every settled row records its
            money as having moved.
        entry_ids: The purchase entry ids the user ticked.
        transaction_ids: The source-row ids the user ticked
            (:attr:`TickForm.ROW`), from either of a row's two lists: the
            write union hands them to BOTH row scopes, which partition on the
            row's account (ruling **R-CC116**, plan step
            ``credit_card:CC-5-4b``).
        corrections: ``{transaction id: amount}`` from the rows' amount
            boxes.  Passed through: the arm decides which of them it may READ
            (ruling **R-FF**) and the settle verb decides whether each is a
            correction or an echo of the prefill.
        transfer_ids: The transfer ids the user ticked (:attr:`TickForm.LEG`,
            ruling **R-BAL145**) -- the TRANSFER's own id, never a shadow's,
            since leaf ``balance:X-bi-6-4c-2``.
        transfer_corrections: ``{transfer id: amount}`` from the transfer
            ticks' amount boxes, passed through on the same terms as
            :attr:`corrections`.  A field of its own because a transfer id and
            a row id can be the same number.
        named: What the panel's captions NAMED, as the page posted them
            back (:class:`~._named.NamedLines`, plan step ``credit_card:CC-5-4a-5``,
            rulings **R-CC76** / **R-CC127** / **R-CC135**): the save's one
            press is graded against :meth:`~._named.NamedLines.for_ticks` over
            :attr:`transaction_ids` (``_assemble.record_reconciliation``).
            Owner input and never a scope: the press only compares it, whole,
            with what the save frees.  A transfer's tick names none, so there
            is no transfer twin.  **Defaults to EMPTY -- every tick named
            nothing**, the default every settle verb under it already has
            (``None``: nothing shown) for the ruled reason: *"A button with no
            warning sends nothing"* (ruling **R-CC127**), so a submission
            built without it refuses any tick that would free a line rather
            than freeing one unannounced.
    """

    statement: Statement
    entry_ids: "set[int]"
    transaction_ids: "set[int]"
    corrections: "dict[int, Decimal]"
    transfer_ids: "set[int]"
    transfer_corrections: "dict[int, Decimal]"
    named: NamedLines = field(default_factory=NamedLines)
