"""ONE derivation of what a review pass over one account may act on.

Plan step ``bank_import:X-f6a-3c-2``.  Reviewing a statement is a pass over one
account, and until this step every ACT inside that pass derived the whole
account again for itself: 3.593 s of :func:`~._candidates.candidates_for` per
accept on the developer's own clone (827 candidate rows), against 215 acts the
screen offers -- **12.88 minutes of derivation to work one statement**, which is
finding **N-306**'s cost and finding **N-309**'s cause.  Measured again at the
end of this step, applying that same statement end to end against ONE of these:
**5.80 s**, of which 3.65 s is this one derivation.

**What belongs in a scope is what a pass CANNOT change**, and that is the whole
rule for what this holds:

* the owner's pay CALENDAR -- nothing here writes a payday;
* the account's governing OPENING -- nothing here restates one;
* every row the account could offer, PRICED (:class:`~._subjects.Candidates`);
* every budget line a bank line could become a purchase against.

**What is deliberately NOT here is what the pass DOES change**: which subjects
a match has already claimed.  Every act re-reads that for itself
(:func:`~._candidates.matched_subjects`) and narrows this scope through
:func:`~._candidates.unmatched_rows`, and re-asks the destinations' own rows
(:func:`~._destinations.current_destinations`), so an item cannot be handed a
row the item before it has just matched or settled.  A scope
that had baked the claims in would have offered 15 of the developer's 91
creatable lines an envelope an earlier proposal in the same pass claims.

**A stale PRICE is NOT safe across a pass, and this paragraph used to say it
was.**  The argument was that a candidate's figure is
``gross - Sigma(card entries) - Sigma(posted purchases)``
(:func:`app.services.cash_ledger.cash_leg_of`), so only a parent and its own
child could move each other -- which the accept door's guard against an
envelope named beside its own purchase refused.  **Measured FALSE on
2026-08-19**: settling a matched purchase writes a SIBLING CC Payback's
``estimated_amount``, which that guard could not see.  *(The guard itself is
deleted since plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1, as
unreachable: a row holding a purchase is worth ``$0.00`` to the offer,
ruling **R-BAL81**; finding **CC-386**, ruling **R-CC144**.)*

**So a price is never taken off this scope.**  What the scope holds is WHICH
rows may be offered, which no act changes;
:func:`~._valuation.repriced` re-reads and re-values every row an act names,
and :func:`~._resolve.resolve_rows` then refuses one that has moved since the
screen described it (finding **N-336**, plan step ``bank_import:X-f6d-3``).
The 3.593 s this step exists to save belongs to the 827-row SCAN, which is
still derived once; an act names one to four rows.  This paragraph asserted the
refuted reason until an adversarial review found it 2026-08-23.

Services-boundary discipline: reads only, plain data in, a frozen dataclass
out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.services import pay_calendar
from app.services.cash_ledger import (
    AmountBasis,
    CashOpeningFact,
    account_opening_fact,
    baseline_amount_basis,
    reject_line_before_books_open,
)

from ._candidates import candidates_for
from ._destinations import destinations_for
from ._creations import PurchaseDestination
from ._offers import BankLine
from ._subjects import Candidates


def impossible_days_refusal(line: BankLine) -> str:
    """Return why nothing can record *line* as a purchase.

    Plan step ``bank_import:X-gm``, finding **N-325**.  **ONE spelling, for the
    reason :func:`no_period_refusal` below is one**: this sentence is READ by
    :func:`~._leftovers._one_creatable` to withhold the ADD control before the
    owner presses anything, and RAISED by
    :func:`~._create.create_purchase_from_line` when a stale page or a crafted
    body reaches the door anyway.  A screen and a door saying different things
    about one rule is what that function's own docstring records having had to
    fix once already.

    **The line's two dates contradict each other and this app does not choose
    between them.**  ``entry_service._reject_settled_before_purchase`` refuses
    a purchase whose money left before it was spent, backed by
    ``ck_transaction_entries_settled_not_before_purchase``, so a purchase from
    such a line cannot exist -- and clamping the purchase day to the earlier of
    the two was refused on 2026-08-19 (developer, finding **N-325**), because
    it decides which day the app believes when the bank contradicts itself.
    That is the substitution ruling **R-FW** refused one clock over.

    Args:
        line: The bank line, which must be one
            :attr:`~._offers.BankLine.states_impossible_days` is true of -- so
            its ``transaction_on`` is not ``None``.  The caller asks that
            predicate and this composes the sentence for it, which is what
            keeps the comparison stated in exactly one place.

    Returns:
        The sentence, naming both of the bank's own days and the act that is
        still open.  It carries no *nothing was changed* clause, for the reason
        :func:`no_period_refusal` carries none: that is true of a refused WRITE
        and false of a control withheld before one, and the door appends its
        own.
    """
    return (
        f"Your bank dates that line as made on "
        f"{line.transaction_on.isoformat()} and taken on "
        f"{line.posted_on.isoformat()}, so its money left before it was "
        f"spent.  A purchase cannot reach your bank before you make it, so "
        f"nothing here can record one.  Match it to a row you already hold "
        f"instead."
    )


def reject_impossible_days(line: BankLine) -> None:
    """Refuse a bank line whose own two dates contradict each other.

    Plan step ``bank_import:X-gm``, finding **N-325**.  **The DOOR's half of
    the withholding the screen renders**, and it exists for the reason ruling
    **R-GJ** cost `$7,412.94` to learn: a control the screen withholds and a
    door that does not refuse is a refusal a crafted body walks around.

    **It fires BEFORE a destination is resolved**, which is
    :meth:`ReviewScope.reject_line_before_books_open`'s placement and its
    reason: ``create_entry`` refuses this pair too, and refuses it AFTER
    :func:`~._create.resolve_destination` may have minted an envelope for a
    purchase that will never exist -- while :mod:`._create`'s own promise is
    that every refusal fires before anything is written.  Until this step that
    promise was FALSE for exactly this class, because the screen dropped these
    lines out of every list and so nothing else ever asked.

    Args:
        line: The bank line the caller is about to record as a purchase.

    Raises:
        ValidationError: When the bank dates the line MADE after it POSTED.  A
            400: an owner working from a page rendered before the withholding
            existed reaches it, and so does a crafted body.
    """
    if line.states_impossible_days:
        raise ValidationError(
            f"{impossible_days_refusal(line)}  Nothing was changed."
        )


def no_period_refusal(day: "date", subject: str) -> str:
    """Return why no budget can hold something placed on *day*.

    **ONE spelling, because it is read in two places and raised in a third**
    (plan step ``bank_import:X-gj-1a``).  :meth:`ReviewScope.period_holding`
    raises it when a write is attempted; :func:`~._leftovers._one_inflow` and
    :func:`~._leftovers._one_creatable` read it to withhold the control
    BEFORE the owner presses anything, which is what keeps a chooser off a
    line the door would refuse.  It was spelled twice before this step, once
    here and once for inflows only, so the screen and the door said different
    things about one rule -- and the OUTFLOW direction said nothing at all
    and rendered a working-looking control (found by adversarial review
    2026-08-29, reproduced on a swipe made the day before the calendar
    opens).

    Args:
        day: The civil day the money is placed by.  **Which day differs by
            subject** and the caller decides: a purchase is placed by the day
            it was MADE, a residual and an income row by the day the money
            POSTED.
        subject: What is being placed, named as the owner would name it --
            "this purchase", "the difference on this match".  A refusal has to
            name the act.

    Returns:
        The sentence.  It carries no *nothing was changed* clause: that is
        true of a refused WRITE and false of a control withheld before one,
        and a reader that printed it beside an offer would be reporting a
        press that never happened.
    """
    return (
        f"No pay period covers {day.isoformat()}, so there is no budget for "
        f"{subject} to belong to.  Extend your pay schedule to cover that "
        f"day first."
    )


@dataclass(frozen=True)
class ReviewScope:
    """Everything one review pass over one account may act on.

    Attributes:
        owner_id: The user the caller proved owns the account.
        account_id: The account whose statement is being reviewed.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`.  ONE per pass for
            everything THIS package asks: three sites here asked
            ``calendar_for`` separately inside one request until adversarial
            financial review 2026-08-19, and two of them can disagree under
            READ COMMITTED -- which since plan step balance:X-i3 is true of
            the POST doors that build a scope and not of the GET that renders
            a screen, whose whole request is one snapshot.
            **FOUR POST doors build one**, re-censused at plan step
            ``bank_import:X-gi-2`` off ``ReviewScope.build``'s own call sites:
            ``statement_reconcile.apply_statement_reconcile``,
            ``.state_reconcile_merchant_rules``, ``.statement_reconcile_match``
            and ``statements.import_statement``.  *This list has now been wrong
            twice in the SAME direction -- it said THREE and named
            ``statement_review_totals``, a route ``X-gf-3b`` deleted, then FOUR
            across the queue and the workbench while OMITTING the import door,
            which has derived a pass since standing rules began filing on
            upload.*  An enumeration quoted as a reason decays invisibly,
            because nobody re-counts a premise -- so this one is now taken from
            the call sites rather than from the surfaces a reader remembers.
            Named by adversarial money review 2026-08-28.
            **ONE of them builds TWO, and that is the one place
            two scopes in one request is right**: ``apply_statement_reconcile``
            takes a FRESH one for the ANSWER, only on the path that WROTE,
            because the pass it was applied against describes a state that no
            longer exists.  It is
            structural rather than remembered now --
            ``_statement_doors.run_statement_fragment_door`` owns which scope
            each arm renders from, so a door cannot report a pass against the
            state that pass replaced.
            **The parameter stays either way, and not only for the arms it
            still protects**: three reads of one fact in one request is this
            project's DRY violation rather than a cost, and the doors it
            protects are the ones that MOVE MONEY.
            **It is not one per REQUEST, and saying
            so would be false**: ``entry_credit_workflow._create_payback``
            reads its own, and a creation filing a purchase into an envelope
            whose payback was soft-deleted reaches it.  Narrowing the claim
            rather than widening the parameter, because that module is another
            arc's.
        basis: The owner's :class:`~app.services.cash_ledger.AmountBasis`.
            ONE per pass, for the reason ``calendar`` above it is one (plan
            step X-au-j, finding **N-309**): every producer under a read pass
            takes the pass's derivations rather than building its own.  It was
            built PER PRICED ROW until this step -- **609 salary-pricing and
            609 loan-pricing constructions** over 825 candidates, `4.7 s` to
            render, with the accept door paying it all again.

            **It belongs in the scope on the scope's own rule**, which is that
            a scope holds what a pass CANNOT change: a basis carries the
            owner's salary derivation and the scenario's loan derivation, and
            no act this package performs writes a salary profile, a payday, a
            loan parameter or an escrow line.  It holds no per-row answer, so
            :func:`~._valuation.repriced` still re-reads every row it values.
        opening: The account's governing
            :class:`~app.services.cash_ledger.CashOpeningFact` -- the day its
            books open and the equity they open holding (plan step
            **balance:X-f3c-2b-2b**).  **On the scope by its own rule**: no
            act this package performs writes ``budget.account_openings``.
            **What it buys is SHARING rather than a saved read**, and a
            first draft of this line said 378 reads, which nothing would
            have made: :func:`~._gaps._split_at_books_open` takes the
            opening as a parameter and would hoist one read whatever this
            field did.  What the field buys is that the split and every
            door read the SAME one, which makes the SCREEN and the DOORS
            provably one answer about which lines the books can hold:
            :func:`~._reads.review_set` splits on it and
            :meth:`reject_line_before_books_open` refuses on it, off one read.
        candidates: Every row the account could offer, priced
            (:func:`~._candidates.candidates_for`), before any claim is
            narrowed out.
        destinations: Every budget line a bank line could become a purchase
            against (:func:`~._destinations.destinations_for`), before any claim
            is narrowed out.
    """

    owner_id: int
    account_id: int
    calendar: "pay_calendar.PayCalendar"
    basis: AmountBasis
    opening: CashOpeningFact
    candidates: Candidates
    destinations: "tuple[PurchaseDestination, ...]"

    def period_holding(self, day: "date", subject: str) -> "pay_calendar.DerivedPeriod":
        """Return the pay period covering *day*, refusing if none does.

        **The PERIOD and not its id since plan step ``balance:X-bi-7b``**: the
        one-off producer the minting doors call dates a placed row at its
        paycheck's START (ruling **R-BAL22**), and that day is on the derived
        period this lookup already found.  Handing back the id alone made the
        producer re-resolve the same period from the same calendar -- a
        second lookup of one fact inside one request -- so the whole value is
        threaded and a caller that needs the id reads ``period_id`` off it.

        **ONE statement of it for the whole package** (plan step
        ``bank_import:X-f6d-4``): :mod:`._create` places a purchase it records
        and :mod:`._variance` places the row it mints for a group's
        difference, and the two had the identical body apart from one noun.
        A second spelling of "which paycheck holds this day" is this arc's own
        root cause 1 on the column that decides which budget a movement lands
        in.

        **It is a method rather than a free function taking a calendar**,
        because the calendar it must use is THIS pass's.  Loading a second one
        is what :func:`~._create.create_purchase_from_line` was doing until
        plan step ``bank_import:X-f6a-3c-2``, and under READ COMMITTED the two
        can differ -- so a line could be OFFERED against one calendar and
        PLACED against another.  Taking it off the scope makes that
        unrepresentable rather than merely avoided.

        **Plan step balance:X-i3 does not retire that argument, and this is
        the one site in the package where saying so needs care.**  Every
        placement runs inside a POST, so the transaction is a command's and
        the two reads really are two snapshots.  It is also the only
        accommodation here whose two halves sit in DIFFERENT requests -- a
        line is offered by the GET and placed by the POST, which no
        per-request snapshot could ever reconcile -- so what makes the pairing
        safe is that the POST re-derives BOTH from one scope of its own, which
        is exactly what this method being a method enforces.

        **WHICH day a caller passes is the caller's decision and they differ**:
        a purchase is placed by the day it was MADE, because it has a budget
        clock of its own, and a residual by the day the match POSTS on,
        because it has none -- it IS the movement.

        Args:
            day: The civil day the money is placed by.
            subject: What is being placed, for the refusal's sentence -- "this
                purchase" or "the difference on this match".  Taken rather
                than composed here, because a refusal an owner reads has to
                name the act they performed.

        Returns:
            The covering :class:`~app.services.pay_calendar.DerivedPeriod`,
            always a SAVED one (its ``period_id`` is set).

        Raises:
            ValidationError: When no SAVED period covers it.  **Both of
            ``period_containing``'s two answers reach here**: a day before the
            owner's first payday, and one past the generated horizon.  The
            review screen splits off only the FIRST
            (:func:`~._gaps._split_at_calendar_open`), so a line posted past
            the last saved period reaches a caller -- which is why callers
            resolve this BEFORE they write anything.
        """
        period = self.calendar.period_containing(day)
        if period is None:
            raise ValidationError(
                f"{no_period_refusal(day, subject)}  Nothing was changed."
            )
        return period

    def reject_line_before_books_open(
        self, posted_on: "date", subject: str,
    ) -> None:
        """Refuse an act over a line this account's books cannot hold.

        **This pass's spelling of the boundary**
        (:func:`app.services.cash_ledger.reject_line_before_books_open`, plan
        step **balance:X-f3c-2b-2b**, finding **N-383**), bound to the opening
        this pass resolved -- the same relationship
        :meth:`~._reads.ReviewSet.search_gap_for` has with
        :func:`~._gaps.search_gap`.

        **It is a method for the reason** :meth:`period_holding` **is one**:
        the opening it must use is THIS pass's, and a door loading a second
        one could judge a line against an opening the screen never saw.  Under
        READ COMMITTED that is a real difference and not a theoretical one --
        both the GET that offers a line and the POST that records it are their
        own snapshots, and a restatement can commit between them.

        **Every door asks it, and the review screen has ALREADY split such a
        line out** (:func:`~._gaps._split_at_books_open`), so nothing this
        pass offers can reach it.  It is live rather than defensive all the
        same: a page rendered before a restatement moved the books forward
        posts line ids the current books no longer hold, and an act naming a
        line the screen never showed is exactly what a form door must refuse
        by name rather than let abort at COMMIT.

        Args:
            posted_on: The day the bank posted the line.
            subject: What is being recorded, named as the owner would name it
                -- "this purchase", "this deposit", "this match".

        Raises:
            ValidationError: When the line posted on or before the day this
                account's books open.  A 400, for the reason
                :meth:`period_holding`'s is.
        """
        reject_line_before_books_open(self.opening, posted_on, subject)

    @classmethod
    def build(cls, owner_id: int, account_id: int) -> "ReviewScope":
        """Derive the scope for one pass over one account.

        The expensive call in the app's whole import path, made once.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account whose statement is being reviewed.

        Returns:
            The :class:`ReviewScope`.

        Raises:
            PayCalendarError: From
                :func:`~app.services.pay_calendar.calendar_for`, when the owner
                has paydays and no resolvable cadence.  Fails loud rather than
                rendering as a designed refusal: a matcher cannot bound a row
                without the calendar that says which paycheck it is budgeted
                in, and answering anyway is the unbounded state finding
                **N-312** records.
            BaselineMissingError: From
                :func:`~app.services.scenario_resolver.require_baseline_scenario`,
                answered by the application-level handler that renders the
                setup-recovery page (ruling **R-BW**).  The RAISING form is
                right here on that ruling's own criterion: this pass's answer
                is UNDEFINED without a scenario, because a basis prices rows
                from a scenario's salary profiles and a scenario's loans and
                there is no honest figure to publish when nobody can say
                which.
            RuntimeError: From
                :func:`~app.services.cash_ledger.account_opening_fact`, when
                the account carries no ``budget.account_openings`` row at all
                -- a broken invariant rather than an empty state, and one this
                pass inherits rather than softens.  Every account gets one at
                creation and migration ``a7c41f9d2b60`` backfilled the rest;
                answering "then every line is recordable" would offer acts on
                money whose level nothing states, which is the fabrication
                that loader exists to refuse.
        """
        calendar = pay_calendar.calendar_for(owner_id)
        # ONE basis for the pass (plan step X-au-j, finding **N-309**).  The
        # BASELINE pin and its Phase-1 deferral are stated once, in
        # ``cash_ledger.baseline_amount_basis``; it matches the ground
        # ``_candidates._transaction_candidates`` already gives for not
        # filtering the candidate scan on ``scenario_id``.
        #
        # A foreign-scenario row is REPORTED rather than raised in this
        # package: ``_settle_price.settle_price`` catches ``AmountUnresolvable`` and
        # the row leaves the candidate set into ``unpriceable``, which the
        # screen counts.  Unreachable today, and NOT the same answer the
        # reconcile panel gives -- said here because a first draft of this
        # comment claimed both passes "fail loud" and only one does.
        basis = baseline_amount_basis(owner_id)
        return cls(
            owner_id=owner_id,
            account_id=account_id,
            calendar=calendar,
            basis=basis,
            # ONE read of the governing opening for the whole pass (plan step
            # **balance:X-f3c-2b-2b**).  The screen splits its lines on it and
            # every door refuses on it, so the two cannot disagree about which
            # lines the books hold.
            opening=account_opening_fact(account_id),
            candidates=candidates_for(account_id, calendar, basis),
            # **The CALENDAR rather than the owner id** since pay-calendar
            # plan step C4-a-4: it IS the ownership scope, and it is where each
            # offered line's paycheck span is derived from, so this producer
            # and ``candidates_for`` above it bound their answers by one value.
            destinations=tuple(destinations_for(account_id, calendar)),
        )
