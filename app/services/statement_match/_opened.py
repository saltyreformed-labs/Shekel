"""The MATCH pane of ONE card: the rows it offers, and what the ticked ones come to.

Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.  **One producer
for a pane two surfaces render**: the live fragment
(``accounts.statement_reconcile_match``) re-derives it on every tick and every
keystroke of the search, and the Reconcile PAGE derives it for the one card a
scriptless request named with ``?open=<line_id>``.  A second assembly would be
two places for a ticked row to be dropped on the way to a door that MOVES
MONEY.

**HOW FAR IT REACHES IS THE CALLER'S FACT** (:class:`MatchReach`), and ruling
**R-BI1** is what decides it (developer, 2026-09-05).  It SUPERSEDES **R-KA**'s
row-set half rather than filling a gap R-KA left: that ruling DID settle the
reach, at the line's own pay period -- *one card is at most 15 rows* -- and
this is the override rather than an answer to an open question.

The scripted pane opens on the line's own pay period and the
SEARCH reaches the rest, which is what keeps a 27-card page off finding
**N-374**; the scriptless render has no search at all, so a period-bounded list
is a bound the owner cannot widen -- which is the cap **N-374** refused.  The
measurement that decides it is :mod:`._panel`'s own (2026-08-30): all 9 of the
9 card payments on the developer's Checking have payback rows their own period
does NOT hold, so the class **R-KA** exists to give an act back is exactly the
class a period-bounded list cannot serve.  The cost is bounded, and it is a
RANGE rather than a figure: rendering this pane's own candidate-row loop body
2026-09-05 gives **570 bytes** for a row with a 30-character label and no
``not_shown_alone`` badge, and about **760** for one carrying that badge, whose
sentence varies.  So 67 unexplained rows on ONE card is roughly **40 KB**,
against the 143,298 bytes **R-KA** rejected for every card's period rows -- and
paid only on a request that exists because the owner asked for that card.
*Plan step ``bank_import:X-gz`` put the row's labelled dates and a gap
sentence under its name, re-measured 2026-09-16 on the macro alone: 529 bytes
to 739 for the same 30-character unbadged row, so the 67-row figure is nearer
**50 KB** now and the argument against R-KA's 143,298 stands unchanged.*

*A first draft of this paragraph quoted one figure and then "reconciled" it
against R-KA's by dividing 143,298 by that render's 311 row renders.  That
arithmetic is wrong and the agreement it manufactured was worth nothing: the
143,298 is the byte total of the PANES, each of which also carries a trigger
wrapper, a search box, a sums block and a consent block, so the quotient is not
a per-row rate at all.  Adversarial review 2026-09-05.*

**EVERY TICKED ROW THE PASS STILL OFFERS IS RENDERED, whatever list this
render is otherwise showing**, and that is not a courtesy: a checkbox that is
not in the document is not submitted.  Without it, ticking a search result and
then searching again takes the first pick silently out of the act -- so a card
payment could not be grouped against two paybacks found by two searches, which
is the very act this module exists to make reachable.  The same hole is what
would make :attr:`MatchReach.EVERY_ROW` unsafe on a browser that DOES have
scripting: it would render all 67, and the first re-price would swap in the
period's 15 and drop whatever the owner had ticked outside them.

**The qualifier is exact and the residue is answered rather than hidden.**  A
submitted row the pass no longer offers -- one another act claimed between two
renders, or a crafted id -- is in no list this walks, so it renders nowhere;
:func:`~._resolve.resolve_rows` refuses that body by name and
:attr:`OpenedMatch.totals` carries the sentence, which is the door's own answer
rather than a silent drop.

**A CLAIM ELSEWHERE USED TO REST ON A MECHANISM THIS MODULE REMOVES, and plan
step ``bank_import:X-gp`` made it structural instead** (ruling **R-BI2**,
superseding **R-IV**).  Until then
:func:`~._variance._reject_unaccepted_difference` argued that a consent ticked
against one remedy and submitted under another was unconstructible BECAUSE the
attribution select's change re-rendered the box unticked -- and nothing swaps
anything on the ``?open=`` render, so this pane held it only on the narrower
ground that no proposal a tier offers is a group with a difference, which
``bank_import:X-gn`` would have re-armed.  The consent is ONE value now, the
figure and the member together (:class:`~._submission.ReviewedDifference`), so
there is no second field for any render to pair differently and X-gn owes the
guard nothing.  **What scripting off cannot do is consent to a set the render
never priced, and the PRESS is what prices it** (plan step
``bank_import:X-gi-2a``, finding **BI-478**).  The ``?open=`` render prices
the proposal's rows and draws the one act for them, so a tier's near miss IS
corrected from this page; a group the owner builds by hand here is priced only
when Apply is pressed, and the door refuses a difference nothing has consented
to.  Until X-gi-2a that refusal re-drew the pane from the PROPOSAL -- the
page's builder handed it the line id and nothing else -- so every tick the
owner had made was gone and, on a card no tier proposed, the pane came back at
`$0.00`.  It is drawn from the SUBMITTED form now (:class:`OpenedAsk`), so the
refusal comes back with the rows ticked, both totals, and the acts the
difference can become; the second press carries the consent.  That is what
this pane's copy says.

**THE RE-DRAWN FORM ECHOES WHAT WAS SUBMITTED, FOR EVERY CONTROL WHOSE VALUE
IS STILL ON OFFER** (ruling **bank_import:R-BI3**, developer 2026-09-11).  A
submitted row renders ticked where the pass still offers it, and a submitted
consent renders picked where an option's WHOLE value -- the figure and the
member's reviewed row token -- equals it (:attr:`OpenedMatch.consent`).  That
is not the pre-selection ruling **R-IU** forbids: the app picks nothing, it
echoes the owner's own tick, and the value it echoes is one the server
spelled.  A row tick that moves the difference leaves no option carrying the
old value, so the pick clears exactly when the act it consented to is no
longer the act on offer -- and survives a search keystroke, which changes no
act at all.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It READS and never writes --
:func:`~._preview.preview_hand_build` is the accept door's own reads and
refusals without the writes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._dating import row_days
from ._panel import MatchCandidates
from ._preview import preview_hand_build
from ._submission import MatchSubmission, as_reviewed, as_reviewed_line

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._dating import RowDays
    from ._offers import BankLine, MatchProposal
    from ._preview import HandTotals
    from ._reads import CardSubject, ReviewSet
    from ._scope import ReviewScope
    from ._subjects import CandidateRow, RowKind
    from ._submission import ReviewedDifference


class MatchReach(enum.Enum):
    """How far one render of the MATCH pane may offer rows.

    **A property of the RENDER and not of the line**, which is why it is a
    parameter rather than a fact on the pass: the same card reaches one list
    through the live fragment and another through ``?open=<line_id>``, and the
    difference is whether the owner can ask a second question.
    """

    #: The line's own pay period, which the SEARCH widens.  What the live
    #: fragment offers, because it is re-derived on every tick and every
    #: keystroke and 67 rows a keystroke is the payload **N-374** is about.
    THE_PERIOD = "period"

    #: Every unexplained row on the account.  What a render with no search
    #: offers, because a bound the owner cannot widen is the cap **N-374**
    #: refused -- and with scripting off there is no keystroke to widen it
    #: with.
    EVERY_ROW = "every"


@dataclass(frozen=True)
class OpenedMatch:
    """What one card's MATCH pane renders.

    Attributes:
        line: The :class:`~._offers.BankLine` the card is about.
        proposal: The :class:`~._offers.MatchProposal` a tier offers for it, or
            ``None``.  Its rows render TICKED (**R-HS**) and are disjoint from
            :attr:`rows` by construction -- the pass withholds every row a
            proposal claims from ``unmatched_rows`` -- so no row renders twice.
        rows: The candidate rows this render offers, in the pass's own order.
            Every list this module can choose is a subsequence of
            :attr:`~._panel.MatchCandidates.every` in that one order, so the
            union with the ticked rows has one order and not two.
        submitted: What the request said about this card, as the door would
            be given it (:class:`~._submission.MatchSubmission`) -- the rows
            it named and the difference it consented to.  **Carried whole,
            and the two echoes are read off it** (:attr:`ticked`,
            :attr:`consent`): the pane re-draws every control the way the
            request submitted it, where the value is still on offer, and one
            value carrying both halves is what keeps a row echo and a consent
            echo from being drawn from two different bodies.  *It carried
            ``ticked`` alone until plan step ``bank_import:X-gi-2a``.*
        query: What the owner typed into the search, or ``""``.  Echoed so the
            box survives its own re-render.
        re_fetches: Whether this pane will ask the server again.  **ONE field
            and not two, because the search and the running difference are one
            fact wearing two hats** (``CLAUDE.md`` rule 14): both work by the
            same round trip, so a pane that re-fetches has both and a pane
            that does not has neither.  Two booleans that are always equal are
            one value with a maintenance contract.

            ``False`` is the scriptless page, and both consequences are
            user-facing.  The SEARCH box would be inert, its label would offer
            a widening that cannot happen, and -- because the pane sits inside
            the Apply form whose only submit is the money button -- pressing
            Enter in it would PRESS THAT DOOR, which is what ruling **R-BI1**
            rejected a scriptless search submit to avoid.  The FIGURES would
            be computed once and stale from the first tick, so the consent
            copy has to say what the door will do at the press rather than
            promise a difference that "appears here".  Adversarial review
            2026-09-05 found the template asserting both.
        totals: What the ticked rows come to and what Apply would do
            (:class:`~._preview.HandTotals`), or ``None`` on a render the
            schema refused before anything could be priced.
    """

    line: "BankLine"
    proposal: "MatchProposal | None"
    rows: "tuple[CandidateRow, ...]"
    submitted: MatchSubmission
    query: str
    re_fetches: bool
    totals: "HandTotals | None"

    @property
    def ticked(self) -> "frozenset[tuple[RowKind, int]]":
        """Return the ``(kind, row_id)`` of every row the request named.

        What renders a row's box checked.  Read off :attr:`submitted` so the
        rows drawn ticked and the rows the pane was priced against are one
        set by construction -- and off
        :attr:`~._submission.MatchSubmission.subjects`, the one producer of
        *which rows a submission names*, rather than a second walk over the
        rows (adversarial review 2026-09-11).

        Returns:
            The subjects, as :attr:`~._submission.ReviewedRow.subject` spells
            them.
        """
        return frozenset(self.submitted.subjects)

    def days_of(self, row: "CandidateRow") -> "RowDays":
        """Return what the pane prints about WHEN for *row*, against this line.

        Plan step ``bank_import:X-gz``, ruling **R-BI9**.  A METHOD the
        template calls per row rather than a field beside :attr:`rows`,
        because the rows arrive on two values -- the proposal's tuple and
        :attr:`rows` -- and one accessor over both is what keeps the two
        lists printing one thing.  It decides nothing: the sentences are
        :func:`~._dating.row_days`', asked against the one line this pane is
        about.

        Args:
            row: A row this pane renders, from either list.

        Returns:
            Its :class:`~._dating.RowDays`.
        """
        return row_days(row, self.line)

    @property
    def consent(self) -> "ReviewedDifference | None":
        """Return what the request said it consented to, or ``None``.

        What renders a consent option PICKED (plan step
        ``bank_import:X-gi-2a``, ruling **R-BI3**): the template draws
        checked the one option whose whole value equals this, and nowhere
        else.  **Read off** :attr:`submitted` **and never off**
        :attr:`totals`, because the preview reads no consent at all (plan
        step ``bank_import:X-gp``): it offers every act the difference can
        become, and which of them the owner has already ticked is a fact
        about the REQUEST, not about the arithmetic.  A consent naming a
        member the rows no longer hold, or a figure the rows no longer come
        to, equals no option and so picks none -- the same answer
        :func:`~._preview.preview_hand_build` gives by ignoring it, reached
        without a second reader of the value.

        Returns:
            The :class:`~._submission.ReviewedDifference`, or ``None`` where
            the request stated none.
        """
        return self.submitted.consent


def proposed_submission(subject: "CardSubject") -> MatchSubmission:
    """Return the submission a card carries before the owner touches it.

    **The value twin of the hidden fields an unopened card renders**: a tier's
    own rows arrive ticked (**R-HS**), so the pane a scriptless request opens
    has to be priced against exactly them or it would report `$0.00` for a
    proposal the card is already offering to apply.

    Args:
        subject: The :class:`~._reads.CardSubject` the page drew this card
            from.

    Returns:
        The :class:`~._submission.MatchSubmission`, naming this line and the
        proposal's rows -- or the line alone for a card no tier paired.

        Its ``consent`` is ``None`` because nothing has been consented to on
        a fresh render, and :func:`~._preview.preview_hand_build` ignores the
        field in any case: it computes the figure the owner is about to be
        shown, so reading one back would be the screen agreeing with itself.
    """
    proposal = subject.proposal
    return MatchSubmission(
        lines=frozenset({as_reviewed_line(subject.line)}),
        rows=frozenset(
            () if proposal is None
            else (as_reviewed(row) for row in proposal.rows)
        ),
        consent=None,
    )


@dataclass(frozen=True)
class OpenedAsk:
    """Which card the Reconcile PAGE opens, and what the request's form holds
    for it.

    Plan step ``bank_import:X-gi-2a``, finding **BI-478**.  **The page's half
    of** :class:`MatchAsk`: the route can say which line was asked for and
    what the body submitted for it, and nothing else -- the subject is
    resolved from the pass the page itself derives, and the reach is fixed at
    :attr:`MatchReach.EVERY_ROW` because that render has no search
    (**R-BI1**).  Until this step :func:`~._reconcile.reconcile_page` took
    the line id alone and priced the pane from
    :func:`proposed_submission` on every render, including the one that
    answers a refused Apply, which is how a refusal came to discard every
    tick the owner had made.

    Attributes:
        line_id: The bank line whose MATCH pane renders in the document
            (:func:`~app.routes.accounts._reconcile_query.asked_to_open`).
        submitted: What the request's form holds for that card, as the door
            would be given it (:class:`~._submission.MatchSubmission`) -- read
            through the same reader and the same schema the live fragment's
            body is -- or ``None`` where the request carries no form holding
            this card at all: the page's GET, and the receipt's own rule form,
            which posts no card.  ``None`` prices the pane against
            :func:`proposed_submission`, the value twin of the hidden fields
            an unopened card renders.  **An empty submission is not
            ``None``**: a body whose every box for this card is unticked
            submits no ``rows-<line>`` field, and that is the owner's answer
            -- nothing ticked -- rather than an absence to fall back from.
            **The route decides which it is, because only the route knows
            what its form carries**; a reader inferring "no form" from the
            absence of a field would read an owner who unticked everything as
            an owner who never touched the card.
    """

    line_id: int
    submitted: "MatchSubmission | None" = None


@dataclass(frozen=True)
class MatchAsk:
    """What ONE render asks of one card's MATCH pane.

    A parameter object rather than four more arguments, which is this
    project's remedy where a PUBLIC function goes over the argument ceiling --
    and these four are one cohesive entity: *which card, holding what, and how
    far it may look*.  The pass it is answered against
    (:class:`~._scope.ReviewScope` and :class:`~._reads.ReviewSet`) is not
    here, because that is a fact about the REQUEST rather than about the ask.

    Attributes:
        subject: Which card, and what claims it
            (:class:`~._reads.CardSubject`).  The caller resolves it, because
            the caller is what answers a line this pass renders no card for --
            a 404 on the fragment, and NO pane on the page, which are
            different answers to the same absence: an ``?open=`` naming a line
            the owner has just applied is stale rather than forged.
        submitted: What the form currently holds, as the door would be given
            it (:class:`~._submission.MatchSubmission`).
        query: What the owner typed into the search.  Blank is not a search,
            and :attr:`reach` is what the list falls back to.
        reach: How far this render may offer rows (:class:`MatchReach`).
            Ignored while :attr:`query` holds a search, which is over every
            unexplained row on the account whoever asked.
    """

    subject: "CardSubject"
    submitted: MatchSubmission
    query: str
    reach: MatchReach


def opened_match(
    scope: "ReviewScope", review: "ReviewSet", ask: MatchAsk,
) -> OpenedMatch:
    """Return everything one card's MATCH pane renders.

    Args:
        scope: The pass's derived offer set
            (:class:`~._scope.ReviewScope`).  **The route builds it**, which is
            the rule every read pass in this project is held to.
        review: The pass (:class:`~._reads.ReviewSet`).  Taken rather than
            derived, so a page and the pane inside it are one walk -- deriving
            a second would be the redundant producer call in one request this
            package treats as a DRY violation rather than a cost.
        ask: What this render asks (:class:`MatchAsk`).

    Returns:
        The :class:`OpenedMatch`.
    """
    candidates = MatchCandidates.of(scope, review)
    # *A ``_still_ticked`` normalisation stood here until plan step
    # ``bank_import:X-gp``*, dropping a consent whose member the owner had just
    # unticked before the preview priced the body.  The preview reads no
    # consent at all now -- it offers every act rather than the one a member
    # named -- and drops the field itself, so the fence is gone rather than
    # kept.
    submitted = ask.submitted
    if ask.query.strip():
        offered = candidates.matching(ask.query)
    elif ask.reach is MatchReach.EVERY_ROW:
        offered = candidates.every
    else:
        offered = candidates.for_line(ask.subject.line)
    shown = {(row.kind, row.row_id) for row in offered}
    return OpenedMatch(
        line=ask.subject.line,
        proposal=ask.subject.proposal,
        # **The offer UNION the owner's own picks**, walked over ``every`` so
        # one order serves both halves: ``for_line`` and ``matching`` are each
        # a subsequence of it, so nothing is reordered by being widened.  The
        # picks are asked of the submission's own ``subjects``, the one
        # producer of which rows it names.
        rows=tuple(
            row for row in candidates.every
            if (row.kind, row.row_id) in shown
            or (row.kind, row.row_id) in submitted.subjects
        ),
        # **What the request said, carried whole and not read.**  The preview
        # below ignores its consent and offers every act; the template draws
        # PICKED the one option whose whole value equals it, so a search
        # keystroke keeps a pick and a row tick that moves the difference
        # clears it (plan step ``bank_import:X-gi-2a``, ruling **R-BI3**).
        submitted=submitted,
        query=ask.query,
        # **THE REACH READ FOR WHAT IT IMPLIES**, and not a second fact: a
        # render that may be NARROWED by a later request is one that HAS later
        # requests.  Asked here rather than in the template, which computes
        # nothing.
        re_fetches=ask.reach is MatchReach.THE_PERIOD,
        totals=preview_hand_build(submitted, scope),
    )


def refused_match(subject: "CardSubject") -> OpenedMatch:
    """Return the pane a render whose submission was REFUSED shows.

    **Nothing is priced and nothing is offered**, because the body never
    reached the door: the schema graded it malformed, so there is no row set
    to price and no figure to show.  The caller renders the sentence beside
    it.

    **IT ECHOES NO QUERY**, and that is a correction rather than a detail
    (adversarial review 2026-09-05).  It carried the submitted one for a
    render, and the template's empty-list arm reads a query as *a search that
    matched nothing* -- so a refused body reported "no unexplained row on this
    account matches that" about a search that was never performed, on a screen
    whose whole job is to be trusted about what it is showing.

    Args:
        subject: Which card (:class:`~._reads.CardSubject`).

    Returns:
        The :class:`OpenedMatch`, carrying the line and its proposal and
        nothing else.  It is the SCRIPTED surface's arm -- the fragment is the
        only caller -- so it says the search works.
    """
    return OpenedMatch(
        line=subject.line,
        proposal=subject.proposal,
        rows=(),
        # **Nothing named and nothing consented to**, because the body that
        # named them was refused whole: the pane draws no box checked.
        submitted=MatchSubmission(
            lines=frozenset({as_reviewed_line(subject.line)}),
            rows=frozenset(),
        ),
        query="",
        re_fetches=True,
        totals=None,
    )
