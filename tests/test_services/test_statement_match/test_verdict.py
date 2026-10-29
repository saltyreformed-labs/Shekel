"""What a standing rule comes to for one pass, and why one did not fire.

Plan step ``bank_import:X-gf-3a``, finding **N-359**.  The verdict is the ONE
value ruling **R-GH**'s automatic door and the review screen both read, so the
cases here grade two things and not one: that each withholding reason fires and
that the door acts on the value rather than re-deriving it.

**The reason the last of those is a MUTATION and not a comparison.** Two
derivations of one rule agree until they drift, so asserting that the door's
sentence equals the screen's would pass on the day before the drift as happily
as on every day after it.  What cannot pass is a door handed a verdict nothing
else could have produced: if it still files the line, it never read one.
"""

from datetime import date, timedelta
from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.pay_calendar import DerivedPeriod
from app.services.statement_match import (
    DAY_WINDOW,
    BankLine,
    CandidateRow,
    CreatableLine,
    MerchantSection,
    NewEnvelope,
    PurchaseCreation,
    PurchaseDestination,
    ReviewBounds,
    ReviewSet,
    RowKind,
    RuleVerdict,
)
from app.services.statement_match._already_held import (  # pylint: disable=protected-access
    ArrivalsAlreadyHeld,
)
from app.services.statement_match._filing import (  # pylint: disable=protected-access
    _rule_filings,
)
from app.services.statement_match._placement import (  # pylint: disable=protected-access
    Placement,
    PlacementKind,
)
from app.services.statement_match._verdict import (  # pylint: disable=protected-access
    ruled,
)

DAY = date(2026, 8, 17)
ENVELOPE_ID = 4821


def _line(line_id=7, day=DAY):
    """Return one unexplained outflow, as the pass hands it out."""
    return BankLine(
        line_id=line_id, posted_on=day, amount=Decimal("-10.89"),
        description="POINT OF SALE DEBIT L340 (Food Lion)",
        transaction_on=None, merchant_id=1, merchant="Food Lion",
    )


def _destination(transaction_id=ENVELOPE_ID):
    """Return the budget line a template answer resolves to."""
    return PurchaseDestination(
        transaction_id=transaction_id,
        name="Groceries",
        category_id=3,
        # The whole paycheck, as ``destinations_for`` reads it off the owner's
        # calendar (pay-calendar plan step C4-a-4) -- the period this record
        # used to hold as three loose fields.
        period=DerivedPeriod(
            period_id=31, period_index=6,
            start_date=date(2026, 8, 6), end_date=date(2026, 8, 19),
            end_is_projected=False,
        ),
        is_settled=False,
        # Every destination names its DEFINITION (plan step balance:X-bi-7d-2).
        template_id=9,
    )


def _creatable(placement, line_id=7, day=DAY):
    """Return one creatable line carrying *placement*."""
    return CreatableLine(
        line=_line(line_id, day),
        pay_period_id=31,
        destinations=(_destination(),),
        placement=placement,
    )


def _records_in(transaction_id=ENVELOPE_ID):
    """Return the placement a template answer produces."""
    return Placement(
        merchant="Food Lion",
        kind=PlacementKind.RECORD_IN,
        destination=_destination(transaction_id),
    )


def _row(kind=RowKind.TRANSACTION, row_id=ENVELOPE_ID, parent_id=None):
    """Return one app row a proposal may name."""
    return CandidateRow(
        kind=kind, row_id=row_id, label="Groceries",
        cash_amount=Decimal("-10.89"), settled_on=DAY, is_settled=False,
        states_own_figure=False, version_id=1, parent_id=parent_id,
    )


def _proposal(row):
    """Return a proposal naming *row* for this line."""
    from app.services.statement_match import MatchProposal  # noqa: PLC0415

    return MatchProposal(lines=(_line(),), rows=(row,), day_gap=0)


def _bounds(**pass_facts):
    """Return the pass limits, publishing only what a case states."""
    return ReviewBounds(
        calendar_opens=None,
        before_calendar_count=0,
        before_calendar_last_day=None,
        crowded_days=pass_facts.get("crowded_days", ()),
        unpriceable_count=pass_facts.get("unpriceable_count", 0),
    )


def _held(total):
    """Return the double-count fact for a period holding *total* of income.

    **The REAL value object, not a stub with a ``total`` on it.**  The sentence
    the pass writes is composed by :meth:`ArrivalsAlreadyHeld.
    why_it_could_double_count` since plan step ``bank_import:X-gj-2b``, so a
    ``SimpleNamespace`` here would be a second implementation of the one thing
    both doors are supposed to share -- and it would pass while that method was
    deleted.  ``rows`` is empty because nothing in this module reads it; what
    the sentence states is the TOTAL.
    """
    return ArrivalsAlreadyHeld(rows=(), total=Decimal(total))


def _lines(creatable, **pass_facts):
    """Return *creatable* ruled under the stated pass facts.

    ``already_held`` defaults to EMPTY -- no line's period holds unexplained
    income -- which is the state every case here was written under and is what
    keeps them about the arm each states.  A case about the double-count
    withholding passes its own map (plan step ``bank_import:X-gj-2b``).
    """
    return ruled(
        creatable,
        pass_facts.get("proposals", ()),
        pass_facts.get("declined_lines", {}),
        _bounds(**pass_facts),
        pass_facts.get("already_held", {}),
    )


def _verdicts(creatable, **pass_facts):
    """Return the verdicts by line id, for the cases that ask about one."""
    return {
        item.line.line_id: item.verdict
        for item in _lines(creatable, **pass_facts)
        if item.verdict is not None
    }


class TestARuleThatReachesNothingProducesNoVerdict:
    """The absence is the answer, and it has to mean exactly one thing.

    A verdict exists for the lines a stated rule names a destination for, so
    ``None`` is *no rule reaches this line* and never *not asked yet*.  Both
    ways of reaching that state are graded, because a receipt saying *your
    rules withheld this* about a merchant with no rule is false -- and a screen
    printing it is worse, since the owner would go looking for an answer they
    never gave.
    """

    def test_a_line_with_NO_placement_has_no_verdict(self):
        """The owner has said nothing about this merchant."""
        assert _verdicts((_creatable(None),)) == {}

    def test_a_line_whose_rule_does_NOT_REACH_it_has_no_verdict(self):
        """A rule exists and resolves to nothing in this line's own period.

        ``UNRESOLVED`` names no destination, so there is no act to perform and
        nothing was withheld: what the screen owes such a line is the
        placement's own ``unresolved_reason``, which it already prints.
        """
        unresolved = Placement(
            merchant="Food Lion",
            kind=PlacementKind.UNRESOLVED,
            unresolved_reason="that envelope has no row in this pay period",
        )

        assert _verdicts((_creatable(unresolved),)) == {}


class TestAVerdictNamesTheActAndWhetherThePassWouldPerformIt:
    """The two halves of the value, each shown against the other."""

    def test_a_clean_line_is_NOT_withheld_and_names_the_rule_s_act(self):
        """The control the withholding cases are read against.

        Without it every case below would pass against a producer that
        withholds unconditionally.
        """
        verdicts = _verdicts((_creatable(_records_in()),))

        assert verdicts[7] == RuleVerdict(
            creation=PurchaseCreation(line_id=7, transaction_id=ENVELOPE_ID),
            withheld=None,
        )

    def test_a_NEW_ENVELOPE_answer_carries_that_arm_of_the_act(self):
        """The other arm of ``PurchaseCreation``, so neither is assumed.

        A new-envelope answer is the developer's own largest rule (Amazon, 26
        lines, `$1,323.06`), and it reaches the door as a different shape --
        no ``transaction_id`` at all -- so a verdict that only carried the
        existing-envelope arm would withhold nothing and file nothing for it.
        """
        creates = Placement(
            merchant="Food Lion",
            kind=PlacementKind.CREATE_NEW,
            new_envelope=NewEnvelope(name="Groceries", category_id=3),
        )

        verdict = _verdicts((_creatable(creates),))[7]

        assert verdict.withheld is None
        assert verdict.creation == PurchaseCreation(
            line_id=7, new_envelope=NewEnvelope(name="Groceries", category_id=3),
        )

    def test_a_line_a_TIER_declined_is_withheld_in_that_tier_s_words(self):
        """The measured arm: the pass admitted a candidate and would not pick.

        12 of the developer's own 80 rule-reachable lines, `$391.77`, on the
        2026-08-26 measurement -- including the `Apple Music` row one day past
        the window from an `Apple` line the door would otherwise have recorded
        a second time.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            declined_lines={7: "one of your own rows is close enough to this"},
        )[7]

        assert verdict.withheld == "one of your own rows is close enough to this"

    @pytest.mark.parametrize("offset", [0, DAY_WINDOW, -DAY_WINDOW])
    def test_a_CROWDED_day_inside_the_pairing_window_withholds(self, offset):
        """A day the group search skipped is a search that was not run.

        All three edges of the window, because the arm is the one
        :func:`~._verdict.search_gap` measures at ZERO on real data: nothing
        reachable would catch a strict-vs-inclusive slip on either end.
        """
        crowded = DAY + timedelta(days=offset)

        verdict = _verdicts(
            (_creatable(_records_in()),), crowded_days=(crowded,),
        )[7]

        assert verdict.withheld is not None
        assert str(crowded) in verdict.withheld

    def test_a_CROWDED_day_beyond_the_window_withholds_NOTHING(self):
        """...and the bound stays a bound.

        Without this the arm would withhold every line on the account the
        moment one day anywhere got crowded, and a door that files nothing is
        indistinguishable from one that is switched off.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            crowded_days=(DAY + timedelta(days=DAY_WINDOW + 1),),
        )[7]

        assert verdict.withheld is None

    def test_an_UNPRICEABLE_row_withholds_the_whole_account(self):
        """Account-wide blindness withholds account-wide.

        The worst of the three arms: one row the amount model cannot value
        stops every rule-reached line on the account, which is why the screen
        has to say so per line rather than leave it a count in a panel.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),), unpriceable_count=2,
        )[7]

        assert verdict.withheld is not None
        assert "could not be priced" in verdict.withheld


class TestAProposalOverTheRuleSDestination:
    """Ruling **R-FZ(d)** applied from the side auto-apply arrives on.

    The pairing that matters is a proposal naming the ENVELOPE WHOLE against
    one naming a PURCHASE inside it, because the two look alike and only the
    first is a collision: 33 of the developer's own 80 rule-reachable lines aim
    at an envelope holding a proposed purchase, and withholding those would
    withhold the whole case the auto-apply door exists for.
    """

    def test_a_proposal_naming_the_ENVELOPE_withholds_the_rule(self):
        """The rule's destination is a row this statement explains on its own.

        **The sentence says what it COSTS, and the cost is not a double
        count.**  Adversarial financial review 2026-08-27 traced the
        arithmetic: a purchase created from a bank line is born posted,
        ``posted_purchase_sum`` counts exactly those, and the cash leg is
        ``gross - off_statement_sum`` -- so filing into the envelope leaves its
        leg unchanged to the cent and no dollar is counted twice, in either
        order.  What it costs is the MATCH: the envelope now holds a purchase,
        so the candidate scan never offers it again (``settle_price``
        prices it ``0``, ``transaction_candidate`` returns ``None``; ruling
        **R-BAL81**) and ``resolve_rows`` refuses any later act naming that
        envelope as a whole as no longer available, and the line the proposal
        explained stays unexplained.  (This docstring named the accept door's
        guard against an envelope named beside its own purchase as the
        refusal until plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1,
        deleted that guard as unreachable: finding **CC-386**, ruling
        **R-CC144**.)  The wording
        ``X-ge`` gave this was *count that money twice*, and this case
        asserted it.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[7]

        assert verdict.withheld is not None
        assert "makes that match impossible to accept" in verdict.withheld
        assert "count that money twice" not in verdict.withheld

    def test_a_proposal_naming_the_envelopes_PAYMENT_withholds_it_too(self):
        """A SETTLEMENT is the row named WHOLE, through its payment.

        Plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43**: a settled row
        is offered as its covering movement, so a proposal naming that
        movement claims the row exactly as one naming the row did -- read
        through :attr:`~app.services.statement_match.CandidateRow
        .transaction_id`, which is the movement's ROW for this kind and the
        movement's id for nothing.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(
                _proposal(_row(
                    kind=RowKind.SETTLEMENT, row_id=99, parent_id=ENVELOPE_ID,
                )),
            ),
        )[7]

        assert verdict.withheld is not None
        assert "makes that match impossible to accept" in verdict.withheld

    def test_a_proposal_naming_a_PURCHASE_INSIDE_it_does_not(self):
        """Two acts naming neither a parent nor its own child."""
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(
                _proposal(_row(
                    kind=RowKind.PURCHASE, row_id=99, parent_id=ENVELOPE_ID,
                )),
            ),
        )[7]

        assert verdict.withheld is None

    def test_a_proposal_over_a_DIFFERENT_envelope_does_not(self):
        """The bound is the destination's id and not the existence of a
        proposal.

        Without this the arm would withhold every rule-reached line on any pass
        that proposes anything at all.
        """
        verdict = _verdicts(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID + 1)),),
        )[7]

        assert verdict.withheld is None


class TestTheGapIsAskedBeforeTheCollision:
    """The property the review screen's single warning rests on.

    The screen prints the withheld sentence where there is one and the search
    gap otherwise, and printing both would print one sentence twice.  That is
    only safe because a verdict withheld for anything OTHER than a gap has no
    gap to hide -- so the order here is load-bearing rather than cosmetic, and
    it is graded on the case where BOTH hold.
    """

    def test_a_line_with_a_gap_AND_a_collision_reports_the_gap(self):
        """A pass that did not finish looking has concluded nothing.

        Naming the collision first would report a conclusion this pass never
        reached: the candidate it threw away might be what the line really is.
        """
        gap = "one of your own rows is close enough to this"

        verdict = _verdicts(
            (_creatable(_records_in()),),
            declined_lines={7: gap},
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[7]

        assert verdict.withheld == gap


def _review(creatable):
    """Return a ReviewSet carrying *creatable* and nothing else.

    Its bounds are EMPTY -- no declined line, no crowded day, nothing
    unpriceable, no proposal -- so a door that re-derived the verdict rather
    than reading the one the line carries would withhold nothing.
    """
    return ReviewSet(
        # **No default on the dataclass, so every construction states it**
        # (plan step ``bank_import:X-gj-4b``): an empty set means *nothing is
        # barred*, and that is the value that reads as safe.  This helper
        # exercises no SKIP and no card-payment merchant, so empty is honest.
        account_payments=frozenset(),
        proposals=(), unmatched=(), unmatched_rows=(),
        creatable=creatable, parked=(), answered_never=(),
        recordable_inflows=(),
        merchants=MerchantSection(merchants=(), templates=()),
        bounds=_bounds(),
    )


class TestTheAutomaticDoorReadsTheVerdictRatherThanDerivingOne:
    """Finding **N-359**'s remedy, graded where it can actually fail.

    The door and the screen describing one limit two ways is what this step
    removed, and the only way to show the door is READING is to hand it a
    verdict the pass facts could not have produced.  Every case below therefore
    carries a ReviewSet whose bounds are EMPTY -- no declined line, no crowded
    day, nothing unpriceable, no proposal -- so a door that re-derived would
    file every one of them.
    """

    def test_a_verdict_carrying_a_reason_WITHHOLDS_despite_a_clean_pass(self):
        """The mutation: withheld under bounds that withhold nothing."""
        review = _review((replace(
            _creatable(_records_in()),
            verdict=RuleVerdict(
                creation=PurchaseCreation(
                    line_id=7, transaction_id=ENVELOPE_ID,
                ),
                withheld="a reason no bound on this pass could have produced",
            ),
        ),))

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == []
        assert [item.reason for item in withheld] == [
            "a reason no bound on this pass could have produced",
        ]

    def test_a_CLEAN_verdict_files_the_act_the_verdict_names(self):
        """...and the same door files the same line when the verdict is clean.

        Paired with the case above rather than standing alone: *nothing was
        filed* is true of a broken door as well as of a working refusal.

        **The act filed is the VERDICT's and not the placement's**, which is
        why the destination here disagrees with the one the placement resolves
        to: a door still reading ``placement.creation_for`` would file into
        ``ENVELOPE_ID``.
        """
        review = _review((replace(
            _creatable(_records_in()),
            verdict=RuleVerdict(
                creation=PurchaseCreation(line_id=7, transaction_id=777),
            ),
        ),))

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == [PurchaseCreation(line_id=7, transaction_id=777)]
        assert withheld == []

    def test_a_line_that_is_not_a_FRESH_swipe_is_neither_filed_nor_withheld(
        self,
    ):
        """The one narrowing that stays in the door (ruling **R-GI**).

        It belongs to the DOOR and to no screen: the review screen shows a line
        whatever import first recorded it, and only this door may act without a
        press, so *new swipe lines only* cannot move onto the verdict.  A line
        outside the fresh set is not withheld either -- nothing about it was
        decided, and a receipt naming it would tell the owner their rules had
        looked at a line from a previous month.
        """
        # **The verdict WITHHOLDS**, and that is what makes this a control.
        # With a clean verdict the withheld arm is never reached, so moving
        # the freshness test BELOW it passed -- and the receipt would then
        # have told the owner their rules withheld a line from a previous
        # month.  Found by adversarial test-quality review 2026-08-27.
        review = _review((replace(
            _creatable(_records_in()),
            verdict=RuleVerdict(
                creation=PurchaseCreation(
                    line_id=7, transaction_id=ENVELOPE_ID,
                ),
                withheld="a reason this pass would give for a fresh line",
            ),
        ),))

        creations, withheld = _rule_filings(review, frozenset())

        assert creations == []
        assert withheld == []

    def test_a_line_with_no_verdict_is_neither_filed_nor_withheld(self):
        """No rule reaches it, so this pass withheld nothing about it."""
        review = _review((_creatable(None),))

        creations, withheld = _rule_filings(review, frozenset({7}))

        assert creations == []
        assert withheld == []


class TestTheSentenceTheScreenPrintsIsComposedHERE:
    """Finding **N-359**'s other half, and the design review's finding.

    The first version of this step set two facts in Jinja and picked between
    them with ``{% if %}``/``{% elif %}``, framing each in template copy.  That
    is a template restating a partition -- the shape this package refuses in as
    many words 200 lines further down the same file -- so the sentence is
    composed here now and printed unbranched.  These cases grade the
    composition, because the template no longer can.
    """

    def test_a_gap_WITHHELD_from_a_rule_reads_as_the_rule_s(self):
        """The rule is named, the pass's own words are quoted, and the advice
        is to go and look."""
        gap = "one of your own rows is close enough to this"

        item = _lines(
            (_creatable(_records_in()),), declined_lines={7: gap},
        )[0]

        assert item.warning.startswith(
            "Your rules will not record this one by themselves:",
        )
        assert gap in item.warning
        # **It names the ACT and not a position** -- ``_LOOK_FIRST`` read
        # "Check the match form BELOW" until plan step ``bank_import:X-gf-3b``
        # moved that form to a surface of its own (ruling
        # **bank_import:R-HC**), at which point a sentence composed in the
        # SERVICE was pointing at a place on a page the service cannot see.
        # **And it names no DIRECTION either** -- it said *as new spending*
        # until plan step ``bank_import:X-gj-2b-3``, at which point ruling
        # **bank_import:R-II** had routed merchant credits into this very
        # pipeline and the sentence was calling a refund spending.
        assert item.warning.endswith(
            "Match it against rows you already hold before recording it.",
        )

    def test_a_COLLISION_advises_the_match_rather_than_the_match_form(self):
        """Different reason, different act -- and that is why the advice is
        composed beside the reason rather than fixed in one template string.

        Sending the owner to the hand-build form here would be wrong: the
        remedy for a destination this statement already explains is to accept
        THAT match, or to file this line somewhere else.
        """
        item = _lines(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[0]

        assert "makes that match impossible to accept" in item.warning
        assert item.warning.endswith(
            "Accept that match first, or file this line somewhere else.",
        )

    def test_the_warning_QUOTES_the_receipt_s_own_sentence(self):
        """One wording, two registers -- asserted rather than assumed.

        The receipt says why the door withheld; the screen says the same thing
        and adds what to do about it HERE.  A screen that composed its own
        wording would be the second spelling this whole step exists to remove,
        and nothing but this case would notice.
        """
        item = _lines(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
        )[0]

        assert item.verdict.withheld in item.warning

    def test_a_line_NO_rule_reaches_still_gets_the_pass_s_own_reason(self):
        """The warning is a WIDER set than the verdict.

        A line nobody has answered for can still be one the pass never
        finished looking at, and the create card is where the wrong act is
        cheapest whether or not a rule is involved.
        """
        gap = "one of your own rows is close enough to this"

        item = _lines((_creatable(None),), declined_lines={7: gap})[0]

        assert item.verdict is None
        # **The sentence NAMES THE ACT, not a position**, and it said
        # "check the match form BELOW" until plan step ``bank_import:X-gf-3b``
        # moved that form to a surface of its own (ruling
        # **bank_import:R-HC**).  A sentence composed in the SERVICE that names
        # where something sits on a page is coupled to a layout the service
        # cannot see; five on that screen carried the coupling and all five
        # went false in one commit.
        # It named no DIRECTION either since plan step
        # ``bank_import:X-gj-2b-3``, for the reason
        # :meth:`test_a_gap_WITHHELD_from_a_rule_reads_as_the_rule_s` states --
        # and this exact string is what the retired queue's ``_notes_for``
        # composed for a parked line and an inflow, through the same function
        # since that step; the sentence outlived it at
        # ``bank_import:X-gi-3`` because the CARD prints it now.
        assert item.warning == (
            f"Before recording this, match it against rows "
            f"you already hold: {gap}."
        )

    def test_a_clean_line_is_told_NOTHING(self):
        """THE FIRING CONTROL for every case above.

        A producer that warned unconditionally would satisfy all of them, and
        a warning on every line is a warning on none.
        """
        item = _lines((_creatable(_records_in()),))[0]

        assert item.verdict.withheld is None
        assert item.warning is None


class TestARefundIsWithheldWhenTheBooksMayAlreadyHoldIt:
    """The double-count guard, asked of the PURCHASE pipeline too.

    Plan step ``bank_import:X-gj-2b``.  ``arrivals_already_held`` guards an
    auto-filed DEPOSIT against recording money the books already hold.  Ruling
    **R-II** routes a container-answered merchant credit into the PURCHASE
    pipeline instead -- and this function, which rules that pipeline, asked
    ``search_gap`` and the proposed-destination test and NOT this one.  A
    hazard the package added a control for was live for the class the very next
    change routed past it.

    **The two guards' fail sets are not nested**, which is why neither
    substitutes for the other: ``search_gap`` reaches ACROSS periods by
    ``DAY_WINDOW`` and fires only where some tier admitted a candidate and
    declined it, while this tests the row's OWN span. A line can pass either
    and fail the other.
    """

    def test_a_refund_whose_period_HOLDS_unexplained_income_is_withheld(self):
        """The money property: a rule does not file it by itself."""
        [line] = _lines(
            (_creatable(_records_in()),),
            already_held={7: _held("2473.38")},
        )

        assert line.verdict is not None
        assert line.verdict.withheld is not None
        assert "count the same money twice" in line.verdict.withheld
        # **The FIGURE as money**, not as a bare ``Decimal``: this sentence is
        # composed in a SERVICE, so no template filter reaches it and
        # ``2473.38`` beside a card reading ``$2,473.38`` is what shipped
        # until plan step ``bank_import:X-gj-2b``'s own review.
        assert "$2,473.38" in line.verdict.withheld
        # The receipt and the screen read ONE sentence, so the advice is on it
        # -- and it names an ACT rather than a SURFACE (ruling
        # **bank_import:R-HC**): this warning is RENDERED on the reconcile
        # screen, so "check it on the reconcile screen" sent the owner where
        # they already were.
        assert "Match it against those rows" in line.warning
        assert "reconcile screen" not in line.warning

    def test_a_refund_whose_period_holds_NOTHING_still_files(self):
        """The control, without which the case above grades a door that
        withheld everything."""
        [line] = _lines((_creatable(_records_in()),), already_held={})

        assert line.verdict is not None
        assert line.verdict.withheld is None
        assert line.warning is None

    def test_the_GAP_is_reported_FIRST_when_both_hold(self):
        """Ordering, which is the sentence the owner reads.

        A line the pass never finished looking at has not been SHOWN to
        collide, so naming the collision first would report a conclusion this
        pass did not reach -- the same ordering the two existing withholdings
        already keep.
        """
        [line] = _lines(
            (_creatable(_records_in()),),
            declined_lines={7: "a day too crowded to search"},
            already_held={7: _held("2473.38")},
        )

        # **BOTH halves** (plan step ``bank_import:X-gj-2b-3``).  This
        # asserted only the ABSENCE of the losing sentence, which a producer
        # that withheld NOTHING also satisfies; the gap is what must be
        # chosen, so the gap is asserted too.
        assert "a day too crowded to search" in line.verdict.withheld
        assert "count the same money twice" not in line.verdict.withheld

    def test_a_COLLISION_is_reported_before_the_double_count(self):
        """The middle rung of the ladder, which NO case reached.

        Plan step ``bank_import:X-gj-2b-3``.  :func:`ruled` chooses one
        withholding from three in a fixed order -- the gap, then a destination
        this statement already explains, then the double count -- and only the
        FIRST boundary was graded.  No case staged a line that was BOTH
        proposed-explained and already-held, so swapping those two ``elif``
        arms changed nothing any test could see.

        **The order is an argument, not an accident.**  A destination this
        statement explains on its own makes the purchase impossible to accept
        (:data:`~._verdict._ALREADY_EXPLAINED`) -- a conclusion this pass HAS
        reached about this line -- while the double count says the money
        *could* already be in the books.  The harder fact is named first, and
        its advice resolves the softer one too: accepting the match, or filing
        the line elsewhere, ends both.

        Asserts the ABSENCE of the other sentence as well as the presence of
        this one, because a producer that concatenated both would satisfy a
        one-sided case while printing two reasons for one line -- the
        two-sentences-per-line shape ruling **bank_import:R-HR** removed.
        """
        [line] = _lines(
            (_creatable(_records_in()),),
            proposals=(_proposal(_row(row_id=ENVELOPE_ID)),),
            already_held={7: _held("2473.38")},
        )

        assert "makes that match impossible to accept" in line.verdict.withheld
        assert "count the same money twice" not in line.verdict.withheld
        assert line.warning.endswith(
            "Accept that match first, or file this line somewhere else.",
        )

    def test_the_double_count_is_reported_when_NO_proposal_collides(self):
        """The control for the case above, on the same two facts.

        Identical staging with the proposal removed: the line is still
        already-held, and now the double count is what it is told.  Without it
        the case above passes on a producer that never reports the double count
        at all -- which is the state this whole class was written to end.
        """
        [line] = _lines(
            (_creatable(_records_in()),),
            already_held={7: _held("2473.38")},
        )

        assert "count the same money twice" in line.verdict.withheld
        assert "makes that match impossible to accept" not in (
            line.verdict.withheld
        )
