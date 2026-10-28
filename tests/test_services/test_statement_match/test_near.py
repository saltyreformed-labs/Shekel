"""The SECOND tier: a figure that nearly agrees, scored rather than gated.

Plan step **bank_import:X-f6d-1**, ruling **R-GD(b)**, finding **N-335**.
:mod:`app.services.statement_match._near` is pure -- lines and candidates in,
proposals out -- so every case here is built from values, exactly as
``test_propose`` is and for the same reason: what a matcher CHOOSES between two
plausible rows is the thing worth asserting, and building a database state per
case would hide it.

**Each refusal below is a FIRING CONTROL.**  This tier's whole risk is that it
offers a pairing the app cannot justify, and every one of its five admission
rules was measured to be load-bearing on the developer's own production clone
2026-08-22:

* the RELATIVE bound -- three cents is 0.017% of the `$178.29` Geico line and
  15% of an `$18.64` swipe, so an absolute one means two different things;
* the MERCHANT -- without it a `Lowe's` swipe pairs with a
  ``CC Payback: Mint Mobile`` row 0.106% away and an `Amazon` swipe with a
  ``Kayla`` envelope 0.339% away, while the genuine `Geico` pairs sit 0.180%
  out, so no distance separates them;
* the row's own FIGURE -- 2 of those 4 wrong candidates are CC paybacks the
  accept door refuses by name, so offering one renders an Accept that cannot
  succeed;
* the DAY WINDOW, shared with the exact tier rather than re-derived;
* being the ONLY candidate on both sides, because two rows that both name the
  merchant have exhausted the evidence and separating them by two basis points
  is arithmetic dressed as a finding.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.statement_match import DAY_WINDOW, NEAR_MISS_BOUND
from app.services.statement_match._near import near_misses
from app.services.statement_match._pairing import days_outside
from app.services.statement_match._offers import BankLine
from app.services.statement_match._subjects import CandidateRow, RowKind
from app.services.statement_match._propose import propose

_DAY = date(2026, 7, 2)

#: The pay period every built row is budgeted in, as all 62 of the developer's
#: own are: 14 days, start to end.  A
#: :class:`~app.services.pay_calendar.DerivedPeriod` since plan step
#: ``bank_import:X-gz`` put the whole paycheck on the row; its identity and
#: projection flag are immaterial to every case here.
_PERIOD = DerivedPeriod(
    period_id=1, period_index=0,
    start_date=date(2026, 7, 1), end_date=date(2026, 7, 14),
    end_is_projected=False,
)


def _line(line_id, amount, posted_on=_DAY, merchant="Geico"):
    """Return one bank line, naming a merchant by default.

    ``merchant`` is the COLUMN ruling **R-GA(a)** made the adapter record, not
    a token parsed from the description -- so ``None`` here is a source that
    names none rather than a line whose merchant is unknown.
    """
    return BankLine(
        line_id=line_id, posted_on=posted_on, amount=Decimal(amount),
        description="ACH DEBIT GEICO PREM COLL", merchant=merchant,
    )


def _row(  # pylint: disable=too-many-arguments
    row_id, amount, settled_on=_DAY, label="Geico",
    states_own_figure=True, transfer_id=None, kind=RowKind.TRANSACTION,
):
    """Return one SETTLED candidate row whose window is its settle day.

    Pylint: too-many-arguments (7/5) -- **each names one admission rule this
    tier applies**, and a case that could not vary them one at a time could not
    show which rule refused.  A parameter object here would be a value with no
    consumer but this file.
    """
    return CandidateRow(
        version_id=1,
        kind=kind, row_id=row_id, label=label,
        cash_amount=Decimal(amount), settled_on=settled_on,
        is_settled=settled_on is not None,
        states_own_figure=states_own_figure, transfer_id=transfer_id,
        period=_PERIOD,
        # A purchase's budget clock is its own day; the period start stands
        # in for it here exactly as ``expected_on`` did before X-gz, and a
        # settled row's window is its settle day either way.
        purchased_on=_PERIOD.start_date if kind is RowKind.PURCHASE else None,
    )


def _reconciled(row_id, amount, made, asserted):
    """Return a purchase the RECONCILE PANEL ticked, whose window is a SPAN.

    The panel stamps the day a BALANCE was asserted for, which is an upper
    bound rather than an observation, so
    :attr:`~._subjects.CandidateRow.expected_window` opens at the day the
    purchase was MADE.  59 of the developer's own 61 reconciled purchases are
    this shape, and no case in this file built one until adversarial review
    2026-08-22.
    """
    return CandidateRow(
        version_id=1,
        kind=RowKind.PURCHASE, row_id=row_id, label="Groceries: Walmart",
        cash_amount=Decimal(amount), settled_on=asserted, is_settled=True,
        states_own_figure=True, parent_id=900,
        purchased_on=made, period=_PERIOD,
        settle_day_basis=SettledDayBasisEnum.ASSERTED,
    )


def _offered(lines, rows):
    """Return ``(proposals, the lines this tier DECLINED)`` as a list and a dict.

    **``near_misses`` returns a mapping since plan step ``bank_import:X-ge-1``**
    -- line id to the sentence this tier gives for its own refusal -- where it
    returned a bare set of the CONTESTED lines.  The cases below assert
    membership, which is what they asserted before; the SENTENCES are graded by
    the cases that name them.
    """
    proposals, declined = near_misses(lines, rows)
    return list(proposals), declined


class TestTheDefectThisTierExists:
    """N-335, as values: the line and the row that booked `$356.61`."""

    def test_the_geico_three_cents_is_OFFERED_with_its_variance(self):
        """Bank line 285 against transaction 2461, three cents apart.

        The exact predicate offered nothing here, the review screen's
        next-cheapest act recorded the line as a NEW purchase, and the ledger
        booked `-178.29` on 07-02 beside `-178.32` on 07-06.
        """
        line = _line(285, "-178.29")
        row = _row(2461, "-178.32", settled_on=date(2026, 7, 6))

        proposals, undecided = _offered([line], [row])

        assert len(proposals) == 1
        assert proposals[0].lines == (line,)
        assert proposals[0].rows == (row,)
        assert proposals[0].difference == Decimal("0.03")
        assert proposals[0].bank_amount == Decimal("-178.29")
        assert proposals[0].app_amount == Decimal("-178.32")
        assert set(undecided) == set()

    def test_the_variance_is_what_the_screen_would_SAY(self):
        """*bank `$178.29`, your row `$178.32`* -- the step's own sentence.

        The template renders these three, so they are asserted here rather
        than only through a rendered page: a proposal that carried the pairing
        and not the figures would put an amount correction in front of the
        owner with nothing saying what it was.
        """
        proposals, _ = _offered(
            [_line(285, "-178.29")], [_row(2461, "-178.32")],
        )

        assert proposals[0].reprices is True
        assert proposals[0].review_class == "reprice"


class TestTheBoundIsRELATIVE:
    """Ruling R-GD(b): a SCORE, never a tolerance -- and never an absolute one.

    The knee is relative because the same cent gap means different things at
    different magnitudes.  Both cases below hold the ABSOLUTE gap constant and
    move only the line's size, so an absolute bound of any value passes or
    fails them together and only a relative one tells them apart.
    """

    def test_three_cents_on_a_large_line_is_INSIDE(self):
        """0.017% of `$178.29`."""
        proposals, _ = _offered(
            [_line(1, "-178.29")], [_row(1, "-178.32")],
        )

        assert len(proposals) == 1

    def test_three_cents_on_a_SMALL_line_is_OUTSIDE(self):
        """0.16% of `$18.64` is inside; 3 cents on `$1.00` is 3%.

        The same absolute gap, an order of magnitude further out in the terms
        this tier scores in.
        """
        proposals, undecided = _offered(
            [_line(1, "-1.00")], [_row(1, "-1.03")],
        )

        assert proposals == []
        assert set(undecided) == set()

    def test_the_bound_is_the_VALUE_the_measurement_fixed(self):
        """`0.005`, named as a literal, because the VALUE is the decision.

        **A first version of this case asserted only against
        :data:`NEAR_MISS_BOUND` itself** -- both its fixtures derived FROM the
        constant -- so it graded the comparison and the relativity and was
        invariant to the number.  Measured by adversarial test-quality review
        2026-08-22: the constant could be loosened to `0.029`, 5.8x, with the
        whole suite green, which would drop the documented margin to the
        nearest measured coincidence (4.76%) from 9.5x to 1.6x and void every
        wrong-pairing measurement the module argues from.

        The value is 0.50% because on the developer's own production clone the
        furthest same-movement pair is **0.180%** of its line and the nearest
        coincidence is **4.76%**.
        """
        assert NEAR_MISS_BOUND == Decimal("0.005")

    def test_a_row_exactly_AT_the_bound_is_in_and_past_it_is_out(self):
        """The comparison itself, at a magnitude the arithmetic is exact at.

        `$5.00` is 0.50% of `$1,000.00` and `$5.01` is not, so the pair
        straddles the operator by ONE CENT -- which is what says the test grades
        `>` rather than a rounding.
        """
        assert len(_offered([_line(1, "-1000.00")],
                            [_row(1, "-1005.00")])[0]) == 1
        assert _offered([_line(1, "-1000.00")],
                        [_row(1, "-1005.01")])[0] == []

    def test_the_bound_scales_the_MONEY_a_proposal_moves(self):
        """What the owner is actually shown at a realistic magnitude.

        The bound is relative, so the correction a single proposal proposes
        grows with the line: `$0.03` on the Geico line this tier was written
        for, and `$12.86` on a `$2,573.38` paycheck line at the same 0.50%.
        Nothing graded that until adversarial test-quality review 2026-08-22 --
        every assertion in the file named a correction of four cents or less.
        """
        proposals, _ = _offered(
            [_line(1, "2573.38", merchant="Clayton")],
            [_row(1, "2586.24", label="Clayton Payroll")],
        )

        assert len(proposals) == 1
        assert proposals[0].difference == Decimal("-12.86")
        assert proposals[0].bank_amount == Decimal("2573.38")
        assert proposals[0].app_amount == Decimal("2586.24")


class TestTheROWMustNAMETheMerchant:
    """The corroboration measurement, as the four shapes it separates.

    Developer decision 2026-08-22, amending R-GD(b) from a tie-break to a
    requirement.  Of the nine leftover lines with a row within 1% on the
    developer's own production clone, the five whose row names the merchant
    are all the same movement and the four whose row does not are all
    undecidable -- and no bound separates them, because the wrong ones sit
    CLOSER than two of the right ones.
    """

    def test_a_row_that_does_not_name_it_is_NOT_offered(self):
        """The `Lowe's` swipe against ``CC Payback: Mint Mobile``, 0.106% out.

        Nearer than the two genuine `Geico` pairs at 0.180%, which is why no
        amount bound can do this job.
        """
        proposals, undecided = _offered(
            [_line(310, "-131.74", merchant="Lowe's")],
            [_row(2772, "-131.60", label="CC Payback: Mint Mobile")],
        )

        assert proposals == []
        # **...and the tier now SAYS it threw that candidate away** (plan step
        # ``bank_import:X-ge-1``).  This case asserted `set() == set()` until
        # then, which is the state that let ruling **R-GH**'s automatic door
        # file over a row the app already held: refusing to PROPOSE and
        # refusing to SAY are different acts, and only the first was ever
        # right.
        assert set(undecided) == {310}
        assert "does not name this merchant" in undecided[310]

    def test_a_line_naming_NO_merchant_offers_nothing(self):
        """``None`` means *this source names none* and corroborates nothing.

        The direction a missing fact has to fail in: an OFX with no merchant
        field proposes no near misses rather than proposing them all.
        """
        proposals, _ = _offered(
            [_line(1, "-178.29", merchant=None)], [_row(1, "-178.32")],
        )

        assert proposals == []

    def test_the_merchant_is_matched_CASE_BLIND(self):
        """The bank shouts and the budget does not.

        `Walmart` against ``Groceries: WALMART`` is one merchant, and a
        case-sensitive compare would withhold the two purchases ruling
        **R-GE** measured.
        """
        proposals, _ = _offered(
            [_line(1, "-121.16", merchant="Walmart")],
            [_row(1, "-121.12", label="Groceries: WALMART")],
        )

        assert len(proposals) == 1

    def test_a_SHORT_merchant_does_not_match_inside_a_longer_word(self):
        """`BP` must not corroborate ``Subprime Loan Payment``.

        ``bank_statement_lines.merchant`` is a ``String(100)`` whose only
        constraint is that it is not blank, so a two-character merchant is
        storable -- and `BP` is a real filling station, not a degenerate input.
        Unanchored containment made it name a word it is merely a substring of,
        which dissolves for that line the one independent fact this tier has.
        Found by two independent adversarial reviews 2026-08-22; the fix
        shipped without this control and a mutation sweep caught that.

        **A minimum LENGTH was rejected in favour of the boundary**, and the
        second case is why: it would refuse `BP` naming a row that really is
        BP's.
        """
        assert _offered(
            [_line(1, "-100.00", merchant="BP")],
            [_row(1, "-100.14", label="Subprime Loan Payment")],
        )[0] == []

        assert len(_offered(
            [_line(1, "-100.00", merchant="BP")],
            [_row(1, "-100.14", label="BP Fuel")],
        )[0]) == 1

    def test_a_merchant_naming_PART_of_a_word_is_not_a_match(self):
        """The same rule from the other end, on a merchant of ordinary length.

        `Amazon` must not corroborate a row called ``Amazonas Travel Fund``:
        the boundary is about the WORD, not about how short the merchant is,
        and a case built only from two-letter merchants would leave that
        untested.
        """
        assert _offered(
            [_line(1, "-100.00", merchant="Amazon")],
            [_row(1, "-100.14", label="Amazonas Travel Fund")],
        )[0] == []

    def test_the_merchant_may_sit_ANYWHERE_in_the_row_label(self):
        """A purchase's label is ``{envelope}: {description}``.

        The two near misses R-GE named are both this shape --
        ``Groceries: Walmart`` -- so a rule anchored at the start of the label
        would refuse exactly the cases the ruling exists for.
        """
        proposals, _ = _offered(
            [_line(1, "-192.39", merchant="Walmart")],
            [_row(1, "-192.24", label="Groceries: Walmart",
                  kind=RowKind.PURCHASE)],
        )

        assert len(proposals) == 1


class TestItOffersNothingTheDoorWouldREFUSE:
    """The Accept-that-cannot-succeed shape, refused before it is rendered.

    Every clause here mirrors one of ``_variance.reject_unrecordable``'s, read
    off the row rather than re-derived -- which is what
    :attr:`~._subjects.CandidateRow.figure_is_correctable` exists for.
    """

    def test_a_row_whose_FIGURE_IS_NOT_ITS_OWN_is_not_offered(self):
        """An envelope worth its purchases, a payback worth what it repays.

        Measured live: 2 of the 4 wrong near candidates on the developer's own
        clone are CC paybacks, and the door refuses both by name -- so without
        this clause the screen offers an Accept button that always fails.
        """
        proposals, undecided = _offered(
            [_line(1, "-178.29")],
            [_row(1, "-178.32", states_own_figure=False)],
        )

        assert proposals == []
        # **...and the tier does NOT report it**, which is a measured decision
        # rather than an oversight (plan step ``bank_import:X-ge-1``).  A row
        # whose figure is whatever its contents come to is the ordinary shape
        # here -- every envelope is one -- so publishing it as *the pass
        # declined a candidate* withheld the very Groceries filing ruling
        # R-GU exists to perform.  The two refusals this tier DOES publish are
        # about evidence for the pairing; this one is about the row's own
        # model.  See ``_near._FIGURE_ADMITTED``.
        assert set(undecided) == set()

    def test_a_TRANSFER_LEG_is_not_offered(self):
        """A transfer's two halves move its ONE figure, so neither is corrected alone.

        Correcting one means correcting the TRANSFER, which is not this door,
        so the pairing is never put in front of the owner as an accept.
        """
        proposals, _ = _offered(
            [_line(1, "-178.29")],
            [_row(1, "-178.32", transfer_id=77, kind=RowKind.LEG)],
        )

        assert proposals == []

    def test_a_SIGN_disagreement_cannot_reach_the_bound(self):
        """Arithmetic rather than a clause, and asserted so it stays true.

        Money leaving is not money arriving, and the door refuses one.  A row
        whose cash sits within the bound cannot have the opposite sign --
        ``|line - row|`` would be ``|line| + |row|`` -- and this case is what
        says so if the bound were ever loosened past 100%.
        """
        proposals, _ = _offered(
            [_line(1, "-178.29")], [_row(1, "178.29")],
        )

        assert proposals == []

    def test_a_line_OUTSIDE_the_row_s_day_window_is_not_offered(self):
        """The pair legality test is the exact tier's, shared not re-derived.

        A bound only one of two passes applies is not a bound, it is a
        disagreement -- which this package has now shipped twice.
        """
        far = _DAY + timedelta(days=DAY_WINDOW + 1)
        proposals, _ = _offered(
            [_line(1, "-178.29", posted_on=far)],
            [_row(1, "-178.32", settled_on=_DAY)],
        )

        assert proposals == []


class TestTheCorrectableAccessorItself:
    """:attr:`~._subjects.CandidateRow.figure_is_correctable` in its own right.

    The composite BOTH the proposer and the accept door ask, reached only
    through :func:`~._near.near_misses` above.  It is asserted directly too
    because it is the one statement of *could the door take a variance here*,
    and a shape it got wrong would show up on this screen as an Accept button
    that always fails.  The door's own sentences per shape are graded in
    ``test_accept``.
    """

    def test_an_ordinary_row_and_a_purchase_are_correctable(self):
        """Both store a figure of their own and belong to no transfer."""
        assert _row(1, "-25.00").figure_is_correctable is True
        assert _row(
            1, "-25.00", kind=RowKind.PURCHASE,
        ).figure_is_correctable is True

    def test_a_DERIVED_figure_is_not(self):
        """An envelope worth its purchases, a payback worth what it repays."""
        assert _row(
            1, "-25.00", states_own_figure=False,
        ).figure_is_correctable is False

    def test_a_TRANSFER_LEG_is_not(self):
        """A transfer's two halves move its ONE figure, so neither is correctable."""
        assert _row(
            1, "-25.00", transfer_id=77, kind=RowKind.LEG,
        ).figure_is_correctable is False


class TestAContestIsREPORTEDRatherThanSettled:
    """The rule that replaced a RANK, on two adversarial reviews' measurement.

    A first implementation ranked the admitted candidates by
    ``(amount distance, day distance)`` and proposed the best.  Both terms were
    then measured incapable of choosing, so the tier now refuses a contest and
    COUNTS it.  Each case below is one of those measurements, kept as a
    regression: they are the inputs that made the rank pick wrong.
    """

    def test_two_admissible_rows_offer_NOTHING_and_are_COUNTED(self):
        """*An ambiguous proposal is a question dressed as an answer.*

        Live on the developer's own data: one `Amazon` line sits the identical
        distance from ``Father's Day`` and from ``CC Payback: Claude Max``.
        The tie-break R-GD(b) reserved for this cannot help, because every
        candidate that gets here already names the merchant.
        """
        proposals, undecided = _offered(
            [_line(1, "-178.29")],
            [_row(1, "-178.32"), _row(2, "-178.32")],
        )

        assert proposals == []
        assert set(undecided) == {1}

    def test_a_TWO_BASIS_POINT_margin_does_not_decide(self):
        """Adversarial design review 2026-08-22, and the module's own proof.

        Two genuinely different `Amazon` purchases, 0.30% and 0.317% from one
        line.  They are not TIED, so a strictly-best rank proposed the first
        and re-priced it -- on a margin two basis points wide, in a module
        whose own header measures that amount distance does not order true
        before false.  The developer's data carries 24 unexplained Amazon lines
        across ten pay periods, so several same-merchant rows in one window is
        the ordinary case for exactly the merchants where this fires.
        """
        proposals, undecided = _offered(
            [_line(1, "-120.00", merchant="Amazon")],
            [_row(1, "-120.36", label="Shopping: Amazon"),
             _row(2, "-120.38", label="Shopping: Amazon")],
        )

        assert proposals == []
        assert set(undecided) == {1}

    def test_a_RECONCILED_span_does_not_let_the_day_decide(self):
        """Adversarial test-quality review 2026-08-22.

        A purchase the reconcile panel ticked carries a SPAN window, and
        :func:`~._pairing.days_outside` scores every day inside a span ZERO --
        so the day term was flat and a two-cent amount margin picked an
        EIGHTY-DAY-OLD purchase over one made on the bank's own day.  It is not
        a corner: 59 of the developer's 61 reconciled purchases share
        ``settled_on = 2026-08-18`` with purchase days up to 128 days earlier,
        so their spans overlap wholesale.
        """
        asserted = date(2026, 8, 18)
        old = _reconciled(7, "-52.10", made=date(2026, 4, 1), asserted=asserted)
        same_day = _reconciled(
            8, "-52.15", made=date(2026, 6, 20), asserted=asserted,
        )
        line = _line(1, "-52.12", posted_on=date(2026, 6, 20),
                     merchant="Walmart")

        assert days_outside(old.expected_window, line.posted_on) == 0
        assert days_outside(same_day.expected_window, line.posted_on) == 0

        proposals, undecided = _offered([line], [old, same_day])

        assert proposals == []
        assert set(undecided) == {1}

    def test_ONE_reconciled_purchase_is_still_offered(self):
        """The positive control for the case above.

        Without it that test passes on a tier that refuses every span window,
        which is a different and much worse behaviour: two of the developer's
        own five near misses are purchases under a settled envelope, which is
        the pair ruling **R-GE** exists for.
        """
        line = _line(1, "-52.12", posted_on=date(2026, 6, 20),
                     merchant="Walmart")
        alone = _reconciled(
            8, "-52.15", made=date(2026, 6, 20), asserted=date(2026, 8, 18),
        )

        proposals, undecided = _offered([line], [alone])

        assert len(proposals) == 1
        assert proposals[0].rows == (alone,)
        assert set(undecided) == set()

    def test_a_row_TWO_lines_admit_goes_to_NEITHER(self):
        """The symmetry, and it is what keeps the offers LEGAL as well.

        Two proposals naming one row would have the second refused at the door
        as already matched, and awarding it to whichever line was iterated over
        first is the greedy shape the exact tier replaced twice.  BOTH lines
        are counted, because both admitted a candidate and neither was decided.
        """
        row = _row(1, "-178.32")

        proposals, undecided = _offered(
            [_line(1, "-178.29"), _line(2, "-178.35")], [row],
        )

        assert proposals == []
        assert set(undecided) == {1, 2}

    def test_an_EXACT_pair_is_not_this_tier_s(self):
        """The exact tiers have already had it, and had it better.

        A near tier that re-offered one would be a second answer to a question
        :func:`~._propose._one_to_one` already answered -- and two proposals
        naming one row have the second refused at the door.
        """
        proposals, undecided = _offered(
            [_line(1, "-178.29")], [_row(1, "-178.29")],
        )

        assert proposals == []
        assert set(undecided) == set()


class TestTheTiersInPROPOSE:
    """Where the near tier sits in the whole pass, and what it may not take."""

    def test_an_EXACT_match_beats_a_near_one_for_the_same_line(self):
        """An exact figure is a categorically stronger claim.

        The near tier sees only what the exact ones left, so a row that agrees
        to the cent can never be displaced by one that nearly does.
        """
        line = _line(1, "-178.32")
        exact = _row(1, "-178.32")
        near = _row(2, "-178.30")

        proposals = propose([line], [exact, near]).proposals

        assert len(proposals) == 1
        assert proposals[0].rows == (exact,)
        assert proposals[0].difference == Decimal("0.00")

    def test_a_row_an_EXACT_proposal_claimed_is_not_re_offered(self):
        """Two lines, one exact row: the near tier may not take it twice."""
        exact_line = _line(1, "-178.32")
        near_line = _line(2, "-178.30", posted_on=_DAY + timedelta(days=1))
        row = _row(1, "-178.32")

        proposals = propose([exact_line, near_line], [row]).proposals

        assert len(proposals) == 1
        assert proposals[0].lines == (exact_line,)

    def test_a_GROUP_is_NEVER_scored_as_a_near_miss(self):
        """Measured: a subset sum can hit any target.

        Over the developer's own leftovers ``n choose k`` puts 20 lines within
        1% of some same-day row set -- a Van Loan transfer landing inside a
        Capital One payment at 0.0064%, TIGHTER than the six genuinely-true
        payroll groups at 0.0019%.  A group's residual is the OWNER's to
        assert (plan step ``X-f6d-4``), never this pass's to guess.

        **The EXACT sum is the control**, so this cannot pass by the rows
        simply being out of the group pass's reach: the same two rows against
        a line they sum to EXACTLY are proposed, and ten cents later they are
        not.
        """
        first = _row(1, "-120.00", label="Geico")
        second = _row(2, "-80.10", label="Geico")

        exact = propose([_line(1, "-200.10")], [first, second]).proposals
        near = propose([_line(2, "-200.00")], [first, second]).proposals

        assert len(exact) == 1
        assert {row.row_id for row in exact[0].rows} == {1, 2}
        assert near == ()

    def test_a_REPRICING_proposal_sorts_LAST(self):
        """Ascending by how much accepting changes, one term further out.

        It is the only proposal on the card that moves an AMOUNT rather than
        only a day, and ruling **R-FZ(c)** gives it its own sweep for that
        reason.
        """
        confirmed = _line(1, "-50.00")
        corrected = _line(2, "-60.00", posted_on=_DAY + timedelta(days=3))
        repriced = _line(3, "-70.00", posted_on=_DAY + timedelta(days=1))

        proposals = propose(
            [confirmed, corrected, repriced],
            [
                _row(1, "-50.00", settled_on=_DAY),
                _row(2, "-60.00", settled_on=_DAY),
                _row(3, "-70.10", settled_on=_DAY),
            ],
        ).proposals

        assert [p.review_class for p in proposals] == [
            "confirm", "correct", "reprice",
        ]

    def test_the_undecided_LINES_ride_out_on_the_pass(self):
        """A score that withholds is a bound, and a silent bound is a sweep.

        This package has twice shipped a bound nobody could see (findings
        **N-315**, **N-322**), so the near tier publishes its own.

        **It publishes the LINE IDS rather than a count** (plan step
        ``bank_import:X-f6d-3``): the screen renders the warning against the
        line it concerns, in the card where recording that line a SECOND time
        is the cheapest act, so a bare number could not be rendered there at
        all.
        """
        proposed = propose(
            [_line(1, "-178.29")],
            [_row(1, "-178.32"), _row(2, "-178.32")],
        )

        assert proposed.proposals == ()
        assert set(proposed.declined_lines) == {1}

    def test_a_line_the_tier_DECIDED_is_not_reported_as_undecided(self):
        """The control that keeps the assertion above from being a tautology.

        A bound that named every scored line would satisfy "it publishes
        something" while telling the owner to hand-build the five matches the
        page had just proposed correctly.
        """
        proposed = propose([_line(1, "-178.29")], [_row(1, "-178.32")])

        assert len(proposed.proposals) == 1
        assert set(proposed.declined_lines) == set()


class TestARowNobodyHasSETTLED:
    """A near miss may also be the act that marks money as having moved."""

    def test_its_day_gap_is_None_rather_than_zero(self):
        """The distance is genuinely UNKNOWN, and the value type says so.

        Reading it as zero captions a row nobody has settled as *confirms the
        day you already had*, which is the collapse ``day_gap`` was made
        three-valued to stop.
        """
        proposals, _ = _offered(
            [_line(1, "-178.29", posted_on=_PERIOD.start_date)],
            [_row(1, "-178.32", settled_on=None)],
        )

        assert len(proposals) == 1
        assert proposals[0].day_gap is None
        assert proposals[0].review_class == "reprice"
