"""The app's four matchable SUBJECTS, as one value the whole package passes.

Split out of :mod:`._offers` at plan step ``credit_card:CC-5-4a-1``, when
that module crossed the 1,000-line bound (ruling **balance:R-IR**: the
session that breaks a module splits it, by SUBJECT).  The seam is the one
:mod:`._offers`' own docstring drew: a :class:`~._offers.MatchProposal` is a
correspondence the app OFFERS between what the bank recorded and what the app
already holds -- and what the app holds is THIS module's value.  A
:class:`CandidateRow` is one app-side subject priced and dated as the app
holds it, :class:`RowKind` says which of the four subjects it is (the
fourth, a transfer's LEG, since leaf ``balance:X-bi-6-4c-1``), and
:class:`Candidates` is an account's whole offerable set.  Nothing here knows
a bank line.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.enums import SettledDayBasisEnum

from ._caveat import NOT_SHOWN_ALONE

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date

    from app.services.pay_calendar import DerivedPeriod

    from ._caveat import NotShownAlone


class RowKind(enum.Enum):
    """Which of the app's four matchable subjects a candidate is.

    **Two facts are tagged by one kind, and a third member is what made
    them two** (plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43**).
    WHICH TABLE the member names -- a
    :class:`~app.models.transaction.Transaction` or a
    :class:`~app.models.transaction_entry.TransactionEntry` -- and WHOSE
    RECORD the candidate's figure, clock and settle door are.  The first two
    kinds agree on both: a PURCHASE is an entry stating its own figure on its
    own day, settled by the entry editor; a TRANSACTION is a row stating its
    figure over its pay period, settled by its status verb.  A SETTLEMENT
    is an ENTRY (the covering movement the status seam writes for a settled
    row, ``transaction_entries.covers_settlement``) whose figure, clock and
    door are its ROW's: the member names the movement, on the account the
    money moved through; the price is the row's record; the window is the
    row's paycheck; a match dates it by re-settling the row.

    **A LEG is one side of a still-planned TRANSFER, on the screen's
    account** (leaf ``balance:X-bi-6-4c-1``, rulings **R-BAL87**,
    **R-BAL159**): its figure is what settling the transfer would book on
    this account (``transfer_service.leg_settle_amount``), its clock is the
    transfer's paycheck, and its door is the transfer's
    (``settle_transfer``).  It is keyed by the TRANSFER's id, not by the
    hidden shadow row the transfer service still writes on this account:
    R-BAL87 makes a leg's identity ``(transfer, account)``, and on ONE
    account's screen the account is the scope's, so the transfer names the
    leg and the kind says it is one (R-BAL159) -- no identity test is asked
    of the id's shape.  It names no entry: the member an accepted match
    records is the covering movement the settle writes, a SETTLEMENT (ruling
    **R-CC43**).  Until this leaf a transfer was offered as its shadow, a
    TRANSACTION, which ``X-bi-6-4d`` would have left with nothing to offer.
    A PAID transfer's side is a SETTLEMENT whose parent is the transfer's
    leg rather than a row (:attr:`CandidateRow.transfer_id` set,
    :attr:`CandidateRow.parent_id` ``None``).

    **The MOVEMENT is the subject of every settled match** (ruling
    **R-CC43**, developer 2026-09-21): a settled row is offered as its
    covering movement on whichever account that movement is on, a Projected
    row as itself, and the member an accepted match records names the
    movement the settle wrote -- never the row.  Through plan step
    ``credit_card:CC-5-3`` a settled row was offered as a TRANSACTION on its
    own account and priced by its movement there, which left a bill charged
    to the card (its movement on the card, its row on checking) with no
    subject on either screen: the card's row-member key
    (``fk_statement_match_members_transaction_account``, dropped with its
    column at plan step ``credit_card:CC-5-4a-2``) refused a row on another
    account, and checking's feed never shows the money.

    **It is TAGGED by the reader that produced the candidate, never derived
    downstream**, which is the rule :class:`OfferKind` states one package over
    after deriving it mis-captioned a live `$1,958.87` reimbursement.  A
    site that asks which TABLE the member names reads :attr:`names_an_entry`;
    a site that asks whose RECORD it is compares against :attr:`PURCHASE`
    (the one kind that states its own) -- and reading the wrong fact is not a
    typo, it is a movement's id handed to a row's door.
    """

    TRANSACTION = "transaction"
    PURCHASE = "purchase"
    SETTLEMENT = "settlement"
    LEG = "leg"

    @property
    def names_an_entry(self) -> bool:
        """Return whether a candidate of this kind names a ``transaction_entries`` row.

        The member table's column, asked of the kind rather than spelled at
        each writer and reader: ``transaction_entry_id`` for a PURCHASE and a
        SETTLEMENT.  A TRANSACTION names a ``transactions`` row and a LEG a
        ``transfers`` row, and neither is ever a MEMBER: the accept door reads
        each back as the covering movement its settle wrote before it records
        the act (``_accept._as_recorded``), so every member names an entry.

        Returns:
            ``True`` for the two entry kinds.
        """
        return self in (RowKind.PURCHASE, RowKind.SETTLEMENT)


@dataclass(frozen=True)
class CandidateRow:  # pylint: disable=too-many-instance-attributes
    """One app row a bank line could be, priced and dated as the app holds it.

    Pylint: too-many-instance-attributes -- **thirteen fields because the
    subject genuinely has thirteen**, not because the value wants splitting.
    It describes ONE row drawn from either of two tables, for six consumers
    that each read a different subset: the proposer reads the amount, the days
    and whether the bank's own figure could be written here, the assignment
    reads the days, the screen reads the label and the kind, the accept door
    reads the kind, the id and the routing links, the unmatched-rows panel
    reads the projection day, and :func:`~._submission.as_reviewed` reads
    the figure and the revision together.  ``TransferSpec`` carries the same
    disable for the same reason.  Two fields were MERGED rather than disabled around:
    ``earliest_day`` and ``expected_on`` were one fact for the only kind that
    has both -- and ``expected_on`` is a PROPERTY now, derived from the two
    facts it was a copy of.

    Attributes:
        kind: Which of the four subjects it is (:class:`RowKind`): the
            table :attr:`row_id` indexes, and whose record the figure, the
            clock and the door are.
        row_id: Its primary key within that table -- the movement's for a
            SETTLEMENT, whose row is :attr:`transaction_id`, and the
            TRANSFER's for a LEG, whose account is the screen's (ruling
            **R-BAL159**).
        label: What to call it on screen.
        cash_amount: Its SIGNED cash effect on this account -- positive INTO,
            the same convention ``bank_statement_lines.amount`` uses, so the
            comparison is a subtraction rather than a sign negotiation.
        settled_on: The day the app currently records the money as having
            moved, or ``None`` for a row that has not been settled at all.
        is_settled: Whether the row is already in the settled band.  A match
            SETTLES the first and CORRECTS the second, and which of the two a
            proposal would do is the one thing a reviewer most needs told.
        states_own_figure: Whether this row's amount is a fact about THIS row
            rather than about some other one.  ``False`` for the two shapes
            ``transaction_service`` publishes a predicate for -- an ENVELOPE
            whose figure is its purchases (``settles_from_entries``) and a CC
            PAYBACK whose figure is the card spend it repays
            (``repays_card_spend``) -- and ``True`` for every purchase, which
            stores its own.

            **It is CARRIED rather than re-asked, because two modules need one
            answer** (plan step ``bank_import:X-f6d-1``).  The accept door has
            always asked it, to refuse a correction the next sibling write
            would silently revert (finding **N-252**); the PROPOSER now needs
            the same fact, because a near miss it offers on such a row is an
            Accept button that can never succeed -- and the proposer is pure,
            with no session to ask.  Carrying it is the shape
            :attr:`settle_day_basis` beside it took for the same reason: a
            fact the row states once, read by whoever needs it, rather than
            two derivations that can disagree.  See
            :attr:`figure_is_correctable`, which is the question those two
            modules actually ask.
        transfer_id: The transfer when this candidate is one side of one --
            a LEG, or a SETTLEMENT that is a leg's payment -- else ``None``.
            Carried because a transfer settles through ``transfer_service``
            and not through the transaction verb, and a writer that had to
            re-derive that would be a second place for the partition to be
            stated.  For a LEG it is :attr:`row_id` again, the one value the
            leg's constructor reads both from.
        parent_id: The envelope a PURCHASE belongs to, or the row a
            SETTLEMENT is the payment of, else ``None`` -- a transaction IS a
            parent and names no other, and a leg's payment pays no row (its
            parent is the transfer, :attr:`transfer_id`).  Carried so the proposer can decline
            to offer a group holding an envelope AND a purchase inside it,
            which the accept door always refuses because the envelope's
            figure already covers its own purchases; without it the screen
            renders an Accept button that can never succeed.  For a
            SETTLEMENT it is the row whose door a match dates it through
            (:attr:`transaction_id`).
        period: The paycheck this row is BUDGETED in, as the calendar derived
            it -- a transaction's own, and for a purchase its envelope's.
            **Carried for BOTH kinds since plan step ``bank_import:X-gz``**
            (ruling **R-BI9**): the MATCH pane prints every row's budgeted
            placement, and a purchase's was on no field at all -- the row
            carried its purchase day under the name ``expected_on`` and its
            envelope's period nowhere, though the constructor had it in hand.
            ``None`` only for a purchase whose period the calendar does not
            carry, which the offer set's own scope makes unreachable; a
            transaction's constructor declines such a row instead.
        purchased_on: The day a PURCHASE was made, which is its budget clock
            (``transaction_entries.purchased_on``, NOT NULL, ruling **R-FW**);
            ``None`` for a transaction and for a SETTLEMENT, whose only
            budget clock is the row's period -- a payment's stored purchase
            day IS its settle day (ruling **R-BAL39**), so it is not a
            second clock and the reader does not carry it as one.
            :attr:`expected_on` and :attr:`expected_through` are DERIVED from
            these two rather than stored beside them, so the span the matcher
            bounds by and the placement the pane prints are one fact.
        settle_day_basis: WHICH KIND of day :attr:`settled_on` is, read
            straight off ``settled_day_basis_id``
            (:class:`app.enums.SettledDayBasisEnum`): ``asserted`` is the
            reconcile panel's UPPER BOUND, ``observed`` is a day a bank
            statement showed, ``entered`` is the owner's own.  ``None`` exactly
            when :attr:`settled_on` is.  The three settle days this package can
            meet are not the same kind of fact and the difference decides a
            window, which is why the basis travels rather than being re-derived
            per asking site.  See :attr:`expected_window`.

            **It was a BOOLEAN derived from ``reconciled_by_id`` until plan step
            X-az** (finding **N-332**), and the column it was derived from
            answered a different question -- WHICH statement was seen to show
            this money, not what kind of day the row records.  The two agreed by
            coincidence of the writers that existed: exact over the panel's
            bound and the bank's observation, and BLIND to the third case, so a
            day the owner typed read as a day the bank had shown.  Carrying the
            basis is not a wider boolean, it is the fact itself.
        version_id: WHICH REVISION of the row this is -- its
            ``OptimisticLockMixin`` counter, read straight off the column.
            Both matchable tables carry one, and SQLAlchemy advances it on
            every ORM-emitted UPDATE of the row.  A SETTLEMENT carries the
            MOVEMENT's counter plus its ROW's
            (:func:`~._valuation.settlement_candidate`): the seam mirrors the
            row's assertion onto the movement, and the row's counter sees
            what the mirror does not (a reverted row moved to another
            paycheck, a rename); both only rise, so the sum rises on any
            change to either.  A LEG carries its TRANSFER's counter, and a
            leg's payment the movement's plus the transfer's
            (:func:`~._leg_valuation.leg_candidate`,
            :func:`~._valuation.leg_settlement_candidate`): the transfer row
            is where a leg's period, status and figure are edited.

            **It is carried for the same reason** :attr:`states_own_figure`
            **is: a second module needs the fact and cannot ask for it.**  A
            review has two moments -- the screen states a correction, the owner
            presses Apply later -- and until plan step ``bank_import:X-f6d-3``
            nothing compared the row the screen described with the row the door
            was about to write (finding **N-336**).
            :class:`~._submission.ReviewedRow` is where that comparison lives,
            and its docstring carries the measurement for why this coordinate
            and the figure are BOTH needed: neither one sees the other's
            writers.
    """

    kind: RowKind
    row_id: int
    label: str
    cash_amount: Decimal
    settled_on: "date | None"
    is_settled: bool
    states_own_figure: bool
    version_id: int
    transfer_id: "int | None" = None
    parent_id: "int | None" = None
    period: "DerivedPeriod | None" = None
    purchased_on: "date | None" = None
    settle_day_basis: "SettledDayBasisEnum | None" = None

    @property
    def transaction_id(self) -> "int | None":
        """Return the ``transactions`` row whose RECORD this candidate is.

        The row a settle door is asked to move (:func:`~._moving._apply_day`)
        and the row a proposal claims WHOLE (:func:`~._verdict`): the
        candidate itself for a TRANSACTION, its row for a SETTLEMENT (plan
        step ``credit_card:CC-5-4a-1``).  ``None`` for a PURCHASE, whose
        record is its own and whose parent is a container rather than the
        subject -- a reader that wants the container reads :attr:`parent_id`
        -- and for a LEG and a leg's payment, whose record is a transfer's
        (:attr:`transfer_id`; leaf ``balance:X-bi-6-4c-1``).

        Returns:
            The row id, or ``None`` for a purchase or a transfer's side.
        """
        if self.kind is RowKind.TRANSACTION:
            return self.row_id
        if self.kind is RowKind.SETTLEMENT:
            return self.parent_id
        return None

    @property
    def expected_on(self) -> "date | None":
        """Return the FIRST day the app believes this row's money could have moved.

        The purchase day for a purchase, the pay period's start for a
        transaction and for a settlement (the row's paycheck, plan step
        ``credit_card:CC-5-4a-1``).  Three consumers, and for a PURCHASE it is one fact doing
        all three jobs: it makes "the bank never showed this" answerable for a
        row carrying no settle day (a projection dated eighteen months out is
        not a payment the bank failed to make -- 712 such rows on the
        developer's own account); on a PURCHASE it is also a FLOOR, since
        ``update_entry`` refuses a settle day before it
        (``_reject_settled_before_purchase``, 23 such pairs measured); and it
        opens :attr:`expected_window`, which is what BOUNDS a row the app has
        never settled.

        Returns:
            The day, or ``None`` for a row the app cannot date at all.
        """
        if self.kind is RowKind.PURCHASE:
            return self.purchased_on
        return None if self.period is None else self.period.start_date

    @property
    def expected_through(self) -> "date | None":
        """Return the LAST such day.

        The pay period's END for a transaction, and the purchase day again for
        a purchase, whose budget clock is a single day rather than a span.
        **It is the half that was missing, and its absence had no bound at
        all**: plan step ``bank_import:X-f6a-3c``, finding **N-312**.  See
        :attr:`expected_window`.

        Returns:
            The day, or ``None`` exactly when :attr:`expected_on` is.
        """
        if self.kind is RowKind.PURCHASE:
            return self.purchased_on
        return None if self.period is None else self.period.end_date

    @property
    def expected_window(self) -> "tuple[date, date] | None":
        """Return the days the app believes this row's money moved between.

        **The one place "when does the app think this happened" is answered**,
        because the answer differs by row kind and by whether the row has been
        settled, and stating it at each asking site is how a whole kind came to
        have no answer at all.

        * a row settled by an OBSERVED day is a point: that ``settled_on`` is
          the day a statement showed the money moving, and an observation beats
          a belief, so the projection is not consulted;
        * a PURCHASE settled by the RECONCILE PANEL spans ``purchased_on`` to
          ``settled_on``, because that day is a BOUND and not an observation --
          see the measurement below.  A BILL ticked the same way keeps its
          point for now, which the same passage explains;
        * a PURCHASE is a point at ``purchased_on``.  Every purchase has one
          -- ``transaction_entries.purchased_on`` is NOT NULL -- so "undated"
          is true of a purchase's CASH clock and false of the purchase.  Plan
          step ``bank_import:X-f6a-3a``, ruling **R-FW**;
        * a TRANSACTION is its PAY PERIOD, start to end.  Its ``expected_on``
          alone is a budgeting fact rather than an observation, so a rule
          reading it as a point would be claiming the app knows a day it does
          not; the period is the span the app actually asserts, and it is the
          whole of what it asserts.  A SETTLEMENT is its ROW's period on the
          same argument (plan step ``credit_card:CC-5-4a-1``): a payment's
          budget clock is the bill's paycheck, and the day it carries is the
          row's settle day mirrored, a point exactly when the row's was.

        **The bound this produces is CADENCE-RELATIVE, and saying so is part
        of stating it.**  The owner's cadence (a ``budget.pay_eras`` row's
        since plan step ``pay_calendar:C17-a``) is user-stated, a day count
        1..365 or a day of the month, so the days a line may be posted on and still
        claim a bill run to the period's length plus twice
        :data:`~._pairing.DAY_WINDOW`: 35 for a weekly owner, 42 for the
        biweekly one this was measured against, 58 monthly, and 393 at an
        annual cadence -- where it is barely a bound at all.  That is the
        honest consequence of bounding a row by what the app itself asserts: an
        owner who budgets in coarser blocks has asserted less about when the
        money moves, and inventing a tighter claim on their behalf is the
        substitution ruling **R-FW** rejected one clock over.  What keeps it
        safe at every cadence is that a proposal is reviewed before it commits
        (**R-FP**).

        **A TRANSACTION answered ``None`` here until plan step X-f6a-3c, and
        that was finding N-312: a bill the app has never marked as paid could
        be claimed by a bank line of any date whatever.**  Measured on the
        developer's own clone: 610 unsettled transactions, 600 of them
        projections dated past the statement's last day, and when the settled
        partner is removed from an amount group **44 of the statement's own
        lines immediately pair with a future projection** -- the worst a
        2026-04-01 line taking a mortgage transfer budgeted 2026-08-27, 148
        days later.  It never fired on the first import only because a settled
        row won every amount race; the second import is where the app's own
        rows have run out.  The earlier reasoning -- that bounding a bill would
        refuse the arm which settles a row nobody has marked as having happened
        -- was re-measured and does not hold: every one of the 51 rows that arm
        settles today is a PURCHASE, already bounded by its own day, and 0
        proposals name an unsettled transaction on either the first pass or the
        second.

        **An ASSERTED settle day is a BOUND, and reading it as a point made
        the matcher blind to the rows it most needed to see.**  The reconcile
        panel stamps the day the owner asserted the BALANCE for --
        ``reconcile_service._purchases.record_settled_days`` says so in as many
        words, *"``settled_on`` is an UPPER BOUND on the true posting day"* --
        while this property read every settle day as the day a statement
        showed.  Two packages, one column, two meanings.

        **The row now SAYS which it holds** (plan step X-az, finding **N-332**).
        This branch asked ``reconciled_by_id IS NOT NULL`` until then, which is
        a different question -- WHICH statement was seen to show this money --
        and it happened to answer the same way for the two writers it met.  It
        could not see the third: a day the owner typed carries no link, so it
        read as a bank observation and got a point.  ``settled_day_basis_id`` is
        the fact itself, and the branch names the member it is about.  Measured on the
        developer's own dev database 2026-08-21: all 61 reconciled purchases on
        Checking carry ``settled_on = 2026-08-18``, and **59 of them sit more
        than** :data:`~._pairing.DAY_WINDOW` **days after their purchase day**
        (worst: 128).  So a point at ``settled_on`` put them out of reach of
        their own bank lines, every such line read as unexplained, and the
        merchant rule offered to RECORD it -- **50 duplicate
        purchases worth `$3,590.00`**, among them a `$18.64` Food Lion the app
        already held on the bank's own day.  The remedy is to say what the app
        actually knows: the money moved between the day the row was budgeted
        for and the day the balance was asserted.

        **PURCHASES only, and that is a measured scope rather than a
        half-finished one** (developer decision 2026-08-22).  The panel stamps
        bills and transfer shadows identically, so the argument for widening
        them is the same -- but the EVIDENCE is not, and neither is the risk.
        ``budget.transactions`` carries **zero** reconciled rows today, so that
        arm would ship on argument alone; and a purchase's floor is a database
        fact (``ck_transaction_entries_settled_not_before_purchase``) while a
        bill's is its pay-period start, which this docstring calls a budgeting
        fact and refuses to read as a point *for that very reason*.  Widening a
        bill therefore opens a span with no floor and no cost signal: a
        `$1,910.95` payment budgeted 2026-01-05..01-18 and reconciled 08-18
        spans **225 days** in which :func:`~._pairing.days_outside` scores
        every day zero, so March, May and July lines of that same amount all
        become legal top-ranked pairings -- against a
        :data:`~._pairing.DAY_WINDOW` measured at 14 precisely so *"a monthly
        commitment cannot reach its neighbour"*.  Found by two independent
        adversarial reviews 2026-08-22.

        **The bound is applied only when it TIGHTENS nothing away.**  A
        reconciled row whose ``expected_on`` falls after its ``settled_on``
        keeps the point: the panel would then be asserting the money moved
        before the app expected it, which bounds the span from ABOVE and says
        nothing about its floor, and inventing one would be the looser reading
        this property refuses everywhere else.  **That branch is UNREACHABLE
        through every door today** and is kept for the reason
        :func:`corrected_purchase_day` keeps its own: a total accessor states
        its impossible case rather than trusting the callers who make it so.
        ``ck_transaction_entries_settled_not_before_purchase`` makes
        ``purchased_on <= settled_on`` a database fact, so no purchase can
        reach it; a pay-period ``start_date`` edit in the ``pay_calendar`` arc
        is what would.

        Returns:
            ``(first, last)``, or ``None`` for a row the app can date no way at
            all -- which the proposer reads as NOT OFFERABLE rather than as
            unbounded (:func:`~._pairing.within_window`).  *A row stating only
            ``expected_on`` was read as a POINT until plan step
            ``bank_import:X-gz``*; both ends are derived from one fact now, so
            a half-stated window is unconstructible rather than tightened.
        """
        first = self.expected_on
        if self.settled_on is not None:
            if (
                self.kind is RowKind.PURCHASE
                and self.settle_day_basis is SettledDayBasisEnum.ASSERTED
                and first is not None
                and first <= self.settled_on
            ):
                return (first, self.settled_on)
            return (self.settled_on, self.settled_on)
        if first is None:
            return None
        return (first, self.expected_through)

    @property
    def figure_is_correctable(self) -> bool:
        """Return whether a bank line's own figure may be WRITTEN to this row.

        **The one statement of "could the accept door take a variance here",
        and it exists because two modules ask it** (plan step
        ``bank_import:X-f6d-1``).  Ruling **R-GD(a)** made the bank's figure
        the record, so a one-to-one match whose sides disagree is RECORDED
        rather than refused -- but three row shapes are still refused, and a
        proposer blind to them would offer a near miss whose Accept can never
        succeed.  That is the shape this package has now named five times.

        The two facts it reads are the row's own, and each is stated once:

        * one side of a TRANSFER cannot be corrected alone -- ``CLAUDE.md``
          transfer invariant 3 holds both sides equal to the parent, so the
          correction is to the TRANSFER.  :attr:`transfer_id` is that fact,
          for a LEG and a leg's payment alike;
        * a row whose figure is not its own to state cannot be corrected at
          all, because the next sibling write reverts it (finding **N-252**).
          :attr:`states_own_figure` is that fact, and it is TWO published
          predicates rather than one -- see that attribute.

        **The refusals that are not here must not be**: a group's difference is
        the owner's to ACCEPT rather than any row's fault (nothing says WHICH
        member it belongs to), a sign disagreement is a fact about the PAIR,
        and a figure too large to store is a fact about the SUM.  Each belongs
        to :func:`~._variance.reject_unrecordable`, which owns everything
        about the two sides disagreeing.

        **A PURCHASE gets no branch of its own**, and the omission is
        deliberate: it stores its own figure and belongs to no transfer, so
        both terms already answer for it and a short-circuit on
        :attr:`kind` would be a second spelling that could disagree with the
        first.  Ruling **R-GE** -- a statement's evidence justifies correcting
        a SETTLED purchase's amount -- bounds that permission by the DOOR
        rather than by the row, so nothing here narrows it either.

        Returns:
            Whether :func:`~._variance._reject_uncorrectable_row` would let a
            differing figure through for this row.
        """
        return self.transfer_id is None and self.states_own_figure

    @property
    def not_shown_alone(self) -> "NotShownAlone | None":
        """Return why the bank never shows this row alone, or ``None``.

        **Server-derived rather than a Jinja branch**, for the reason
        :attr:`~._bars.BarredLine.reason` is: a template restating a partition
        is a second place for it to be wrong, and this one is a claim about
        money -- the panel that reads it is asserting the bank failed to make a
        payment.  :attr:`app.services.statement_import.BankAnchor.unconfirmed`
        makes the same argument for the same kind of decision.

        **It reads** :attr:`states_own_figure` **and asks nothing else**, which
        is what keeps this a naming rather than a second derivation: that
        attribute is already carried and already published by
        ``transaction_service`` as two predicates.  It is a NARROWER question
        than that attribute answers, and the two are not the same fact -- a row
        whose amount is a fact about some OTHER row is one the bank accounts
        for through that other row, which is a SUFFICIENT reason for the bank
        never to show it alone and not a necessary one.

        **The converse is deliberately not claimed, and its gap is measured
        rather than assumed.**  A ``None`` here means the app cannot PROVE the
        bank would have shown this row separately, not that it would have.  Two
        shapes on the developer's own panel answer ``None`` and are arguably
        never their own line: the ``Phone Allowance`` and ``Health Insurance
        Allowance`` rows, which arrive inside one payroll deposit; and the 7
        envelope-tracked containers holding no entries (see
        :data:`NOT_SHOWN_ALONE`).  Neither carries a fact that says so, and the
        only predicates that would claim them -- a row-name list, or
        ``tracks_purchases`` -- would withdraw the alarm from rows the bank may
        really have failed to show.  **A predicate with no false positives and
        stated gaps is the honest shape here**; the alternative is the
        allowlist this project removes rather than writes.

        Returns:
            :data:`NOT_SHOWN_ALONE`, or ``None`` when the row states its own
            figure.
        """
        return None if self.states_own_figure else NOT_SHOWN_ALONE


@dataclass(frozen=True)
class HeldElsewhere:
    """A row planned on the screen's account whose payment is recorded on another.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2a), ruling **R-CC137**
    (developer 2026-10-04, "Only Paid from, say why"): *"Checking's statement
    screen does not offer Hotel while its payment is recorded on the Visa (the
    reconcile panel's test, R-CC126), and says so on that screen: 'Hotel
    $120.00 is planned on Checking but its payment is recorded on the Visa, so
    it is not offered here. If Checking paid it, press Paid in Hotel's popover
    with Paid from set to Checking.'"*  The sentence's WORDS are ruling
    **R-CC140**'s since (:attr:`said`), which replaced the one quoted here.
    The FACTS the sentence names, carried beside the offer set for the reason
    :attr:`Candidates.unpriceable` is: a row the screen does not offer and
    does not mention leaves its bank line looking unexplained, and recording
    that line as new spending would count the money twice while the row stays
    Projected.

    Attributes:
        name: The row's name.
        figure: What pressing Paid on the row records NOW, in the row's own
            terms, on every state a door writes (the one exception, a DATED
            payment under a Projected row, is
            :func:`~._valuation.held_elsewhere_of`'s) (ruling **R-CC139**,
            developer 2026-10-04: *"Don't display
            $120 if the bill is $135."*): the price the OTHER account's
            screen offers the same payment at
            (:func:`~._valuation.held_elsewhere_of`), so a plan edited since
            the payment was kept reads at its new figure, as it does there
            -- where the stored movement's column still holds the old one.
            A figure the owner TYPED is kept across the revert and honoured
            by Paid (ruling **R-BAL61**), so it reads that figure, plan edit
            or not: the lane's application of ruling **R-BAL207** to the
            stated case, which the R-CC139 question never showed.
        recorded_on: The name of the account its payment is recorded on.
        is_income: Whether the row's money comes IN (a paycheck, a refund),
            which picks the sentence's verb.
    """

    name: str
    figure: Decimal
    recorded_on: str
    is_income: bool

    @property
    def said(self) -> str:
        """Return the screen's sentence for this row, as ruling **R-CC140** picked it.

        Ruling **R-CC140** (developer 2026-10-04, "Short", replacing
        **R-CC137**'s sentence), with its made-up refund figure swapped here
        for another (ruling **R-BAL132**): *"'Electric $135.00 is not listed
        here because the app has it as paid from Visa. To change that, edit
        Electric on the grid.' A refund: 'Refund $38.00 is not listed here
        because the app has it as received into Visa. To change that, edit
        Refund on the grid.'"*  The verb follows the row's direction
        (:attr:`is_income`), and the account is named as the owner named it,
        with no article.  Composed here beside the facts it names, the shape
        :attr:`~._gaps.BooksBound.said` has, from the same query that
        withholds the row: every row named is one the offer set withheld,
        and a withheld row goes unnamed only when it is worth ``0.00``
        (offered on no screen) or cannot be priced (counted among the
        unpriceable instead).

        Returns:
            The sentence.
        """
        moved = "received into" if self.is_income else "paid from"
        return (
            f"{self.name} ${self.figure:,.2f} is not listed here because the "
            f"app has it as {moved} {self.recorded_on}. To change that, edit "
            f"{self.name} on the grid."
        )


@dataclass(frozen=True)
class Candidates:
    """The rows a statement could be showing, and the ones nothing could price.

    Facts that must travel together: a screen listing what it could match
    and saying nothing about what it could not read as a clean sweep, which is
    the "no silent caps" discipline applied to a money screen.  Two kinds of
    row are not offered and said: the ones nothing could price, and since
    leaf 5c-2a the ones whose payment is recorded on another account.

    Attributes:
        rows: The offerable candidates: the settled records (SETTLEMENT --
            rows' and legs' payments together, by recorded day), then the
            Projected rows (TRANSACTION), then the still-planned transfer
            legs (LEG), then the purchases
            (:func:`~._candidates.candidates_for`).
        unpriceable: The ``(kind, row_id)`` of every subject that could not
            be priced -- a row or a leg the amount model had no rule for,
            counted in the screen's "could not be priced" note (a leg whose
            transfer's twin pair was damaged was counted too from leaf
            ``balance:X-bi-6-4c-1``, ruling **R-BAL158**, until ruling
            **R-BAL235** at ``X-bi-6-4d-2`` priced a leg off its transfer
            alone).  Keyed by subject rather than by a bare id because two
            tables' ids now share it.  Empty on today's data -- every
            production row still owns its figure -- and live from the first
            per-kind cutover
            (plan step ``balance:X-au-d``).  They are NOT candidates: a
            matcher that offered a row it could not price would be guessing.
        held_elsewhere: The rows planned on this account that are NOT offered
            here because their payment is recorded on another account (ruling
            **R-CC137**), one :class:`HeldElsewhere` each, in row-id order --
            the second thing the screen must say about what it did not offer.
            A held row nothing could price is among :attr:`unpriceable`
            instead, and one worth ``0.00`` is in neither, as a ``0.00`` row
            is offered nowhere (:func:`~._valuation.held_elsewhere_of`).
    """

    rows: "list[CandidateRow]"
    unpriceable: "tuple[tuple[RowKind, int], ...]"
    held_elsewhere: "tuple[HeldElsewhere, ...]"
