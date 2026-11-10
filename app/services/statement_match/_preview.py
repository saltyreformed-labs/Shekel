"""What the hand-build form's two sides come to, BEFORE anything is applied.

Plan step ``bank_import:X-f6d-4``, ruling **R-FN**.

**Ruling R-FN says a difference is a transaction the owner ACCEPTS, and you
cannot accept a figure you have not seen.**  The proposals on the review screen
each state their own correction because the server built them; a group the
owner assembles from two checkbox lists is theirs, so nothing has computed it
yet.  This is what computes it.

**It runs the ACCEPT door's own reads and refusals, minus the writes.**  The
body it takes is the body Apply would send -- the same line ids and the same
reviewed row tokens, read through the same
:func:`~app.schemas.validation.statement_reconcile.reconcile_match_payload`
-- so the panel is not a second opinion about the act, it is that act asked
what it would do.
A screen that summed differently from the door is finding **N-336** one tier
up, and the surest way to have one number is to have one derivation.

**There is no longer an ordering token in that body**, and its absence is plan
step ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**).  It carried
``apply=hand`` while this form shared a page with the reviewed pass, where a
non-numeric index was the only thing keeping its ticks out of proposal ``0``'s
submission -- a money-correctness hazard held off by the two controls being
separate ``<form>`` elements, which is a property of the DOCUMENT.  The form is
a surface of its own now and posts a group with no index at all, so there is no
shared namespace left for two acts to collide in.

**It is a READ and it never writes** (:func:`~._accept.record_match` is the
only thing that does).  What it can do is REFUSE early: a group naming a
transfer shadow, or a row worth whatever its purchases are, is one the door
will not record, and saying so beside the checkboxes is better than saying it
after the press.

**The browser computes nothing.**  A first version of this step summed the two
sides in JavaScript from ``data-cash`` attributes and posted the result back as
the consent.  Three things were wrong with that and all three go away here: the
project's own coding rule says *JS never computes monetary values*; the
submitted figure was quantized by the schema with ``ROUND_HALF_EVEN``, the mode
``app.utils.money`` forbids, so a sub-cent figure was silently repaired into
agreement; and the same total ended up spelled four ways on one card.  Now the
server renders the figure and every option of the consent control carries the
server's own value.

**THE OPTIONS ARE THE ACTS** (plan step ``bank_import:X-gp``, ruling
**R-BI2**).  This used to decide ONE remedy from the member the owner had named
in a separate select and render one consent box whose label described it; the
door compared only the figure, so two acts submitted one consent.  It now
offers every act a difference can become -- **R-FN**'s ordinary row, and the
bank's figure written to each member -- as one option each, labelled with the
figures that act writes and valued with a
:class:`~._submission.ReviewedDifference` carrying the figure AND the member.
Which act the owner picks is no longer something this function is told; it is
what the door is told, once, in the value of the option they ticked.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It READS and never writes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.exceptions import ValidationError

from ._candidates import matched_subjects
from ._subjects import CandidateRow
from ._resolve import resolve_lines, resolve_rows
from ._scope import ReviewScope
from ._submission import MatchSubmission, ReviewedDifference, as_reviewed
from ._sides import MatchSides
from ._landing import DifferenceLanding
from ._variance import landing_refusal, reject_unrecordable

#: What the panel is doing about the gap, as one word the template dispatches
#: on.  A STRING rather than a ref-table id because it names a state of this
#: screen and no table holds it; the template compares it, which is the
#: sanctioned pattern for a computed domain string (see ``_money_macros``).
#:
#: *It named two more until plan step ``bank_import:X-gp`` -- ``corrects``
#: and ``records``, the remedy the panel had decided from a separately
#: submitted member.*  The panel decides no remedy now: it offers all of them
#: as :attr:`HandTotals.options`, so :data:`DIFFERS` is the one state a
#: non-zero difference has.
NOTHING_TICKED: str = "nothing"
AGREES: str = "agrees"
DIFFERS: str = "differs"
REFUSED: str = "refused"

#: What a panel describing NO act reports for its two sides.  One value rather
#: than two zeros written at each of the three sites that need them, so an
#: empty panel and a refused one cannot come to report different nothings.
_NO_SIDES: MatchSides = MatchSides(bank=Decimal("0.00"), app=Decimal("0.00"))


@dataclass(frozen=True)
class DifferenceOption:
    """One act a match's difference can become, as the pane offers it.

    Plan step ``bank_import:X-gp``, ruling **R-BI2**.  **An option IS an act**:
    the value it submits names the figure and the member together, and the
    figures the pane labels it with are read off the landing that value
    decides -- one derivation, rendered as one option, read back as one value.
    A first design of this pane drew the landing as a select and the consent
    as a box whose label was composed from that select, and the door compared
    only the figure; :class:`~._submission.ReviewedDifference` carries the
    worked case.

    Attributes:
        accepts: The value the control submits when this option is ticked
            (:class:`~._submission.ReviewedDifference`): the difference, and
            the member it lands on or ``None`` for **R-FN**'s ordinary row.
        landing: Where the difference goes under this option
            (:class:`~._landing.DifferenceLanding`), which is what the label's
            figures are read from.  **For a match naming ONE row it names that
            row while** :attr:`accepts` **names none**, and that is ruling
            **R-GD**'s determinacy rather than a disagreement: there is nothing
            to choose, so the value states the figure alone and the door's
            :func:`~._landing._named_member` answers the row, exactly as it
            does for the figure a tier's unopened card states.
        refusal: The door's own sentence where this landing cannot hold the
            figure the bank leaves it (:func:`~._variance.landing_refusal`),
            else ``None``.  Rendered as a DISABLED option carrying the
            sentence, ruling **R-HW**'s shape: an option the door would refuse
            is not an act, and an option that vanished would leave the owner
            unable to see why a member they can tick is not one the gap can
            land on.
    """

    accepts: ReviewedDifference
    landing: DifferenceLanding
    refusal: "str | None" = None

    @property
    def value(self) -> str:
        """Return what this option submits, as the wire spells it.

        **A STRING and not the value object**, because it is a wire value:
        rendering a ``Decimal`` through Jinja would let the template's own
        repr decide what the owner submits.  One writer,
        :attr:`~._submission.ReviewedDifference.token`.
        """
        return self.accepts.token

    @property
    def corrects(self) -> "CandidateRow | None":
        """Return the row this option writes the bank's figure to, or ``None``.

        **The ROW and not its name**, because the label needs its label and
        its current figure, and composing either in Jinja would be a second
        spelling of a fact the landing already states.

        Returns:
            The member the difference lands on, or ``None`` where this option
            is **R-FN**'s ordinary row.
        """
        return self.landing.on_row

    @property
    def corrects_to(self) -> "Decimal | None":
        """Return what the bank says :attr:`corrects` is worth.

        On :attr:`~._subjects.CandidateRow.cash_amount`'s own convention, so the
        sentence quotes the ROW's two figures rather than the match's two sums
        -- which for a lone row are the same two numbers and for a group are
        not (plan step ``bank_import:X-gj-3a``).

        Returns:
            The figure, or ``None`` beside a ``None`` row.
        """
        return self.landing.bank_cash


@dataclass(frozen=True)
class HandTotals:
    """What the hand-build panel shows, decided in the service.

    **Every field is rendered and none is computed by the template.**  Which
    acts a difference can become, what each writes, and whether the door would
    refuse one are all decisions -- and deciding any of them in Jinja would be
    a second statement of :class:`~._landing.DifferenceLanding`'s rule in a
    language nothing lints.

    **It stores the DOOR's own answers rather than copies of their parts**
    (plan step ``bank_import:X-gj-3a``).  It carried ``bank``, ``app`` and
    ``difference`` as three fields, which is :class:`~._sides.MatchSides`
    written out twice -- the third of them being that class's own subtraction
    -- so the sides are HELD and read through properties, and every reader of
    ``totals.bank`` or ``totals.difference`` is unchanged.  *It also carried
    ONE landing and ONE remedy until plan step ``bank_import:X-gp``*, decided
    from a member the owner had named in a separate control; it carries every
    landing now, one per :attr:`options` entry, and decides no remedy at all.

    Attributes:
        sides: What the two ticked halves come to
            (:class:`~._sides.MatchSides`), which is where :attr:`bank`,
            :attr:`app` and :attr:`difference` are read from.
        remedy: One of the module constants, saying what this panel is doing
            about the gap.
        consent: The value an AGREEING match submits as the difference it was
            reviewed against -- ``"0.00"`` with no member, spelled by the one
            writer every consent value has -- or ``None`` on every other panel.
            **The figure travels even where there is nothing to agree to**,
            since plan step ``bank_import:X-gj-1b`` deleted the consent gate's
            exempt shape: the field is what says *this is the figure I was
            shown*, and a match that skipped it would be one the door cannot
            check.  It is a hidden field because at zero the two writers a
            difference has are both idle (the gate's own argument), so there is
            no act to ask permission for.  **A STRING and not a Decimal**,
            because it is a wire value.
        refusal: Why Apply would refuse whatever the owner picked, in the
            door's own sentence, or ``None``.
        options: Every act this difference can become, one
            :class:`DifferenceOption` each, in the order the pane renders
            them; empty where there is no act to consent to (nothing ticked,
            one side ticked, agreeing sides, or a selection already refused).

            **A match naming ONE row offers ONE**, the bank's figure written
            to that row, because ruling **R-GD**'s group clause (ii) answers
            the question of which member and there is nothing else it could
            become.  A GROUP offers **R-FN**'s ordinary row FIRST -- which is
            what every group did before ``X-gj-3a`` -- and then one option per
            member in the order :func:`~._resolve.resolve_rows` returns them.
            Asked of :meth:`~._landing.DifferenceLanding.offers_a_choice`,
            the value that owns the question, so the control the panel draws
            and the landing the door derives cannot disagree.

            **None is pre-selected**: the developer ruled on 2026-09-01
            (**R-IU**) that NO member is pre-selected, on **R-HX**'s reading
            of what justified means -- the app has a candidate for a payroll
            group (the member whose figure is computed rather than stored)
            and not a justification.  **No later step supplies one**:
            ``X-gj-3b`` was withdrawn 2026-09-02 (**R-JJ**), and ``salary:R18``
            removes the CHOICE instead of justifying it, by making one payroll
            deposit name one row.  The control is NOT retired with it -- a
            genuine multi-row group has no computed member, so it has no
            candidate either.
    """

    sides: MatchSides
    remedy: str
    consent: "str | None" = None
    refusal: "str | None" = None
    options: "tuple[DifferenceOption, ...]" = ()

    @property
    def bank(self) -> Decimal:
        """Return what the ticked statement lines come to, signed."""
        return self.sides.bank

    @property
    def app(self) -> Decimal:
        """Return what the ticked rows come to, on the same convention."""
        return self.sides.app

    @property
    def difference(self) -> Decimal:
        """Return what the bank moved that those rows do not account for."""
        return self.sides.difference

    @property
    def needs_consent(self) -> bool:
        """Return whether the owner has something here to AGREE to.

        Plan step ``bank_import:X-gj-1b``.  **The control's question, answered
        here rather than by a template comparing** :attr:`remedy`, because the
        two are not the same question and reading one for the other is how a
        money control comes to render in a state nobody designed: every panel
        that states an act submits a figure, and only some of them are asking
        permission for it.

        Returns:
            ``True`` where this panel offers acts whose difference is non-zero
            -- the control whose options say what each would write.
            ``False`` both where the sides agree, which is the same figure as
            a hidden field because there is nothing to permit, and where there
            is no act at all (nothing ticked, or a selection already refused),
            which submits no figure and renders none.
        """
        return bool(self.options)

    @property
    def offers_a_choice(self) -> bool:
        """Return whether :attr:`options` are ALTERNATIVES rather than one act.

        What decides the widget: alternatives are a radio group, so picking
        one unpicks the rest, and a single act is a box the owner can tick and
        untick.  Read off the options this panel was built with rather than
        counted in Jinja, and the options were built off
        :meth:`~._landing.DifferenceLanding.offers_a_choice`, so this is that
        predicate's answer seen from the panel and not a second spelling of it.

        Returns:
            ``True`` where more than one act is offered.
        """
        return len(self.options) > 1

    @classmethod
    def untouched(cls) -> "HandTotals":
        """Return the panel for a form with nothing ticked on one side."""
        return cls(sides=_NO_SIDES, remedy=NOTHING_TICKED)

    @classmethod
    def refused(cls, sentence: str, sides: "MatchSides | None" = None):
        """Return the panel for a selection this door would not record.

        **One constructor for the three places that build one**, so a refused
        panel cannot end up describing its figures differently depending on
        which refusal produced it -- including the route's own, where the
        submission never reached a service at all and there are no sides to
        show.

        Args:
            sentence: The refusal, in the door's own words.
            sides: What the two halves came to, where they were derived
                before the refusal fired; ``None`` where nothing got that far.

        Returns:
            The :class:`HandTotals`.
        """
        return cls(
            sides=_NO_SIDES if sides is None else sides,
            remedy=REFUSED,
            refusal=sentence,
        )


def preview_hand_build(
    submission: MatchSubmission, scope: ReviewScope,
) -> HandTotals:
    """Return what the ticked lines and rows come to, and what Apply would do.

    Args:
        submission: What the form currently holds -- the same value
            :func:`~._accept.accept_match` would be given if the owner pressed
            Apply now.  **Its ``consent`` is IGNORED, both halves**: this
            function computes the figure the owner is about to consent to, so
            reading one back would be the screen agreeing with itself; and
            since plan step ``bank_import:X-gp`` it offers EVERY act the
            difference can become rather than the one a submitted member
            named, so there is nothing about the landing to be told.  *It read
            the member half until that step, and a stale member -- one the
            owner had just unticked, posted by a select not yet re-rendered --
            needed normalising before :func:`~._resolve.resolve_rows` would
            price the body; the consent is dropped whole here instead, which
            deletes that fence rather than keeping it.*
        scope: The pass's derived offer set.

    Returns:
        The :class:`HandTotals`.
    """
    if not submission.line_ids and not submission.rows:
        return HandTotals.untouched()
    # **The consent goes before anything is priced**, so a stale pointer
    # posted with a legal untick is not answered with a refusal written for a
    # crafted body (``resolve_rows`` refuses a consent naming a row the body
    # does not carry, correctly, at the door that WRITES).
    ticked = replace(submission, consent=None)
    matched = matched_subjects(scope.account_id)
    try:
        # **NOT locked**: this is the preview, and a query request runs
        # in a READ ONLY transaction where PostgreSQL refuses every row
        # lock.  See :func:`~._resolve.load_lines`' ``for_write``.
        lines = resolve_lines(
            ticked, scope.account_id, matched, for_write=False,
        )
        rows = resolve_rows(ticked, scope, matched)
    except ValidationError as exc:
        # A line another match has claimed, a line whose day the bank has
        # restated (ruling **R-BI14**), or a row that moved since the page
        # was drawn.  The door would refuse the same way, so the panel says so
        # now rather than letting the press discover it.
        return HandTotals.refused(str(exc))

    sides = MatchSides.of(lines, rows)
    if not lines or not rows:
        # **One side ticked shows its total and offers NOTHING**, which is the
        # honest answer rather than the empty one: the owner has picked
        # something and the panel says what it comes to, but a match needs both
        # halves (``_accept._reject_empty_side``) so there is no act to consent
        # to yet.  A first version returned the empty panel here and reported
        # `$0.00` for a `$2,573.43` line the owner had just ticked -- caught by
        # driving the real screen in a browser.
        return HandTotals(sides=sides, remedy=NOTHING_TICKED)
    try:
        # **Asked with the difference as the reviewed figure**, which is the
        # question the panel is for: *if you consented to this, what would
        # happen?*  Every refusal that is about the ROWS or the PAIR fires
        # here, so the screen names it beside the checkboxes.  **With NO
        # landing**, since plan step ``bank_import:X-gp``: the one refusal
        # that is about a landing is asked of every option separately below,
        # because the pane offers every landing rather than the one a
        # submitted member named.
        #
        # It read ``sides.difference or None`` until plan step
        # ``bank_import:X-gj-1b``, and the two spell the same behaviour: the
        # gate returns early on a zero difference whether it is told ``None``
        # or ``Decimal("0.00")``.  The straight value is passed because it
        # states the question this preview is asking -- *against THIS figure*
        # -- where the ``or None`` spelled a zero difference as *nothing was
        # reviewed*, which is not what the panel means and is not what it
        # renders.  (An earlier draft of this comment claimed the ``or None``
        # would now make the preview refuse every agreeing match.  It would
        # not; the claim was written against a version of the gate that did
        # require a figure at zero, and was not revisited when that changed.)
        reject_unrecordable(rows, sides, sides.difference)
    except ValidationError as exc:
        return HandTotals.refused(str(exc), sides)

    # **The figure travels whether or not there is anything to agree to**, and
    # that is the developer's ruling of 2026-08-30: every match states the
    # difference it was reviewed against, and a match whose sides agree was
    # reviewed against a difference of nothing.  What differs between the two
    # arms below is only the CONTROL -- a zero difference is a hidden field,
    # because there is nothing for an owner to consent to, and a non-zero one
    # is the control whose options say what each act would write.
    if not sides.difference:
        return HandTotals(
            sides=sides, remedy=AGREES,
            consent=ReviewedDifference(figure=sides.difference).token,
        )
    return HandTotals(
        sides=sides, remedy=DIFFERS, options=tuple(_options(sides, rows)),
    )


def _options(sides: MatchSides, rows: "list[CandidateRow]"):
    """Yield every act a non-zero difference over *rows* can become.

    **One landing per option, derived exactly as :func:`~._accept.record_match`
    derives the one it is told** (:meth:`~._landing.DifferenceLanding.of`),
    so the figures each option is labelled with and the figure the door
    writes for it are one derivation.  The door is then given the option's
    own value and derives that landing again from it; nothing here is a
    second opinion.

    **Offered as a CHOICE only where there is one to make, asked of the value
    that owns the question.**  One row is answered by ruling **R-GD**'s group
    clause (ii): the one act is the bank's figure written to that row, and its
    value names no member because there is none to name -- the shape the
    figure a tier's unopened card states already has.  Several is the case
    plan step ``bank_import:X-gj-3a`` made attributable and ``X-gp`` made one
    control: **R-FN**'s ordinary row first, then each member.  A first version
    of the pane spelled ``len(rows) > 1`` here and the door spelled it again,
    which is one predicate in two modules.

    Args:
        sides: What the two halves come to, derived once for the whole act.
        rows: The match's app rows, already priced, in the order
            :func:`~._resolve.resolve_rows` returns them.

    Yields:
        The :class:`DifferenceOption` for each act.
    """
    if not DifferenceLanding.offers_a_choice(rows):
        landing = DifferenceLanding.of(sides, rows, None)
        yield DifferenceOption(
            accepts=ReviewedDifference(figure=sides.difference),
            landing=landing,
            refusal=landing_refusal(landing, sides),
        )
        return
    yield DifferenceOption(
        accepts=ReviewedDifference(figure=sides.difference),
        landing=DifferenceLanding.of(sides, rows, None),
    )
    for row in rows:
        landing = DifferenceLanding.of(sides, rows, (row.kind, row.row_id))
        yield DifferenceOption(
            accepts=ReviewedDifference(
                figure=sides.difference, on_row=as_reviewed(row),
            ),
            landing=landing,
            refusal=landing_refusal(landing, sides),
        )
