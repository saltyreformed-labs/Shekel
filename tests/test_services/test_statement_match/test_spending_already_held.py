"""Money going OUT that the books may already hold in another shape.

Ruling **bank_import:R-BI19**, plan step **bank_import:X-f6b-2**, finding
**N-381**.  The twin of ``test_income``'s safeguard class for the direction
that had NO per-line signal at all.

**The step's own acceptance shape.**  On the developer's 2026-09-15 import,
21 of the 150 undisposed lines were purchases he had logged by hand at another
amount or day and 10 were bills whose app amount differs.  A row more than
half a percent from the line is never admitted by the near tier and never
reported (``TOO_FAR`` is not published), so ``search_gap`` could not see it,
and a standing rule filed the swipe a SECOND time -- once a night, under the
daily feed, with nobody reading the screen.  Measured on the production
restore (X-f6b-2's handoff s.3d-1): the fact fires on 14 of 51 creatable
outflows, 11 of them lines the gap does not reach, and needlessly on 1 of 20
accepted exact matches.

**What the ruling promises, and what each case below pins:**

* a hand-logged purchase NAMED for the line's merchant is found at ANY
  figure, and so is a bill so named;
* an unexplained purchase INSIDE the definition the owner's rule files this
  merchant into is found whatever it is called;
* the bound is the day window the pass already pairs across, and NOT the
  amount -- the inflow value's proof is deliberately not copied;
* a bill whose label does not name the merchant and no rule reaches is the
  known limit, pinned so it cannot be mistaken for a defect;
* an ARRIVING row is not spending, a row a proposal claims is not asked
  about, and the screen and the door read ONE value.

Every case runs the real pass (``review_set`` over ``a_scope``) rather than
the value alone, because what N-381 measured missing was the pass's answer.
"""

from datetime import timedelta
from decimal import Decimal

from app.services.statement_match import review_set
from app.services.statement_match._already_held import (  # pylint: disable=protected-access
    ArrivalsAlreadyHeld,
    SpendingAlreadyHeld,
)
from app.services.statement_match._pairing import (  # pylint: disable=protected-access
    DAY_WINDOW,
)

from ._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    an_envelope,
    an_import,
)

MERCHANT = "Food Lion"


def _a_swipe(seed_user, statement, *, amount="-54.12", merchant=MERCHANT, **kw):
    """Stage one unexplained outflow from *merchant* under *statement*.

    Args:
        seed_user: The seeded user bundle.
        statement: The import the line belongs to.
        amount: Signed, negative OUT of the account.
        merchant: What the bank names the merchant.
        **kw: Passed through to :func:`a_bank_line`.

    Returns:
        The staged line.
    """
    return a_bank_line(
        seed_user, statement, amount=amount, merchant=merchant,
        description=f"POINT OF SALE DEBIT L340 ({merchant})", **kw,
    )


def _the_fact_for(seed_user, line):
    """Return what the pass says the books may already hold *line* as.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded outflow.

    Returns:
        The creatable line's ``already_held``, or ``None``.

    Raises:
        AssertionError: When the pass does not offer *line* to the create
            door at all -- which is what a PROPOSED line looks like, and a
            case about that state says so itself rather than reading
            ``None`` as *nothing held*.
    """
    review = review_set(a_scope(seed_user))
    for item in review.creatable:
        if item.line.line_id == line.id:
            return item.already_held
    raise AssertionError(
        f"line {line.id} is not creatable in this pass: "
        f"proposals={[p.lines[0].line_id for p in review.proposals]}"
    )


class TestAHandLoggedRowNamedForTheMerchantIsFound:
    """Shape (1): the merchant's name, at any figure."""

    def test_a_purchase_logged_at_ANOTHER_amount_is_named(
        self, app, db, seed_user,
    ):
        """`$50.00` logged for a `$54.12` swipe: 7.6% apart, the near tier's
        bound is 0.5%, and no rule is stated -- the row is found by name."""
        groceries = an_envelope(seed_user)
        logged = a_purchase(
            seed_user, groceries, amount="50.00", description="Food Lion",
        )
        line = _a_swipe(seed_user, an_import(seed_user))

        held = _the_fact_for(seed_user, line)

        assert isinstance(held, SpendingAlreadyHeld)
        assert [row.row_id for row in held.named_for_merchant] == [logged.id]
        assert held.inside_destination == ()
        assert held.total == Decimal("-50.00")
        assert held.why_it_could_double_count == (
            "your records already hold $50.00 leaving that no bank line "
            "explains, named for Food Lion, so recording it automatically "
            "could count the same money twice"
        )

    def test_a_BILL_named_for_the_merchant_at_another_figure_is_named(
        self, app, db, seed_user,
    ):
        """GEICO: a `$100.00` bill against the bank's `$101.37`, 1.37% out."""
        bill = a_transaction(seed_user, name="GEICO auto", amount="100.00")
        line = _a_swipe(
            seed_user, an_import(seed_user), amount="-101.37",
            merchant="GEICO",
        )

        held = _the_fact_for(seed_user, line)

        assert held is not None
        assert [row.label for row in held.named_for_merchant] == [bill.name]

    def test_NO_narrowing_by_amount_in_either_direction(
        self, app, db, seed_user,
    ):
        """The inflow's proof is not copied: the amount is what is wrong.

        A `$5.00` row is smaller than the swipe and a `$500.00` row is larger;
        the deposit-side proof would drop the first, and a mirror of it
        would drop the second.  Both are found.
        """
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="5.00", description="Food Lion",
        )
        a_purchase(
            seed_user, groceries, amount="500.00", description="Food Lion",
        )
        line = _a_swipe(seed_user, an_import(seed_user))

        held = _the_fact_for(seed_user, line)

        assert held is not None
        assert sorted(row.cash_amount for row in held.rows) == [
            Decimal("-500.00"), Decimal("-5.00"),
        ]


class TestARowInsideTheRulesEnvelopeIsFound:
    """Shape (2): the container's identity, whatever the row is called."""

    def test_a_purchase_called_ANYTHING_in_the_destination_is_named(
        self, app, db, seed_user,
    ):
        """``weekly shop`` in Groceries, under a Food Lion -> Groceries rule."""
        groceries = an_envelope(seed_user)
        logged = a_purchase(
            seed_user, groceries, amount="150.00", description="weekly shop",
        )
        a_rule(seed_user, MERCHANT, template_id=groceries.template_id)
        line = _a_swipe(seed_user, an_import(seed_user))

        held = _the_fact_for(seed_user, line)

        assert held is not None
        assert held.named_for_merchant == ()
        assert [row.row_id for row in held.inside_destination] == [logged.id]
        assert held.destination == "Groceries"
        assert "inside Groceries" in held.why_it_could_double_count

    def test_a_purchase_in_ANOTHER_envelope_is_not(self, app, db, seed_user):
        """The container is the rule's, not the account's."""
        groceries = an_envelope(seed_user)
        gas = an_envelope(seed_user, name="Gas")
        a_purchase(seed_user, gas, amount="47.61", description="fill-up")
        a_rule(seed_user, MERCHANT, template_id=groceries.template_id)
        line = _a_swipe(seed_user, an_import(seed_user))

        assert _the_fact_for(seed_user, line) is None

    def test_a_line_NO_rule_reaches_has_no_container_to_look_in(
        self, app, db, seed_user,
    ):
        """Without a rule there is no destination, so only shape (1) can find
        anything -- and ``weekly shop`` does not name Food Lion."""
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="150.00", description="weekly shop",
        )
        line = _a_swipe(seed_user, an_import(seed_user))

        assert _the_fact_for(seed_user, line) is None

    def test_a_row_found_BOTH_ways_is_counted_once(self, app, db, seed_user):
        """A ``Food Lion`` purchase inside Groceries under the Groceries rule."""
        groceries = an_envelope(seed_user)
        logged = a_purchase(
            seed_user, groceries, amount="50.00", description="Food Lion",
        )
        a_rule(seed_user, MERCHANT, template_id=groceries.template_id)
        line = _a_swipe(seed_user, an_import(seed_user))

        held = _the_fact_for(seed_user, line)

        assert [row.row_id for row in held.named_for_merchant] == [logged.id]
        assert [row.row_id for row in held.inside_destination] == [logged.id]
        assert len(held.rows) == 1
        assert held.total == Decimal("-50.00")
        assert "named for Food Lion or inside Groceries" in (
            held.why_it_could_double_count
        )


class TestTheBoundIsTheDayWindow:
    """The one bound, and it is the pass's own (``DAY_WINDOW``)."""

    def test_a_row_BEYOND_the_window_is_not_found(self, app, db, seed_user):
        """A row the pass would never pair is not one it warns about.

        **The row is asserted to be ON OFFER first**: ``unmatched_rows``
        holds only rows whose window overlaps the recorded span, so with one
        line in the later period the bootstrap-period purchase would be
        absent from the set entirely and this case would measure that
        absence rather than the window (the trap ``test_income``'s twin
        case records).  A second line in the bootstrap period keeps the
        span wide.
        """
        later = a_later_period(seed_user)
        start = seed_user["bootstrap_period"].start_date
        groceries = an_envelope(seed_user)
        logged = a_purchase(
            seed_user, groceries, amount="50.00", description="Food Lion",
            purchased_on=start,
        )
        statement = an_import(seed_user)
        _a_swipe(
            seed_user, statement, amount="-3.33", merchant="Kobo",
            posted_on=start,
        )
        far = start + timedelta(days=DAY_WINDOW + 1)
        assert far >= later.start_date, "the far day must be in a saved period"
        line = _a_swipe(seed_user, statement, posted_on=far, sequence_in_group=1)

        review = review_set(a_scope(seed_user))

        assert logged.id in {row.row_id for row in review.unmatched_rows}
        assert _the_fact_for(seed_user, line) is None

    def test_a_row_AT_the_window_s_edge_IS_found(self, app, db, seed_user):
        """The boundary from the other side, so the case above cannot pass
        on a value that returns ``None`` for everything."""
        a_later_period(seed_user)
        start = seed_user["bootstrap_period"].start_date
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="50.00", description="Food Lion",
            purchased_on=start,
        )
        statement = an_import(seed_user)
        # The span-keeping line, for the reason the case above gives.
        _a_swipe(
            seed_user, statement, amount="-3.33", merchant="Kobo",
            posted_on=start,
        )
        line = _a_swipe(
            seed_user, statement, posted_on=start + timedelta(days=DAY_WINDOW),
            sequence_in_group=1,
        )

        assert _the_fact_for(seed_user, line) is not None


class TestWhatIsNotSpendingTheBooksHold:
    """The rows the fact never names, each for its own reason."""

    def test_an_ENVELOPE_named_for_the_merchant_is_not_money_the_books_hold(
        self, app, db, seed_user,
    ):
        """A row priced from OTHER rows holds nothing they do not.

        Both new-envelope forms offer the MERCHANT as the envelope's name,
        so under the feed an envelope called ``Food Lion`` holding a
        ``Food Lion`` purchase is the ordinary case.  The envelope is a
        negative-cash row in ``unmatched_rows`` (kept there for the
        hand-build form) whose label names the merchant -- and it is priced
        at that very purchase.  Named beside it, the fact said `$100.00` for
        one `$50.00` row, and the envelope stayed *named for* every later
        swipe into it from the night the automatic door filed the first:
        the ruling's rejected every-swipe-warns shape, reached by the
        default name.  Only the purchase is money the books hold.  Found by
        this leaf's adversarial review 2026-09-18; a CC payback is the same
        fact (``states_own_figure``), graded at ``test_candidates``.
        """
        food_lion = an_envelope(seed_user, name=MERCHANT)
        logged = a_purchase(
            seed_user, food_lion, amount="50.00", description=MERCHANT,
        )
        line = _a_swipe(seed_user, an_import(seed_user))

        held = _the_fact_for(seed_user, line)

        assert [row.row_id for row in held.rows] == [logged.id], (
            "the envelope, priced at this purchase, was named beside it"
        )
        assert held.total == Decimal("-50.00")

    def test_an_ARRIVING_row_is_not_spending(self, app, db, seed_user):
        """A ``Food Lion`` refund is money arriving: the other fact's."""
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="-20.00", description="Food Lion",
        )
        line = _a_swipe(seed_user, an_import(seed_user))

        assert _the_fact_for(seed_user, line) is None

    def test_a_row_a_PROPOSAL_claims_is_not_asked_about(
        self, app, db, seed_user,
    ):
        """The false-positive control: an exact match is the proposer's.

        A `$54.12` ``Food Lion`` purchase and a `$54.12` line are paired by
        the exact tier, so that line is not creatable at all and the row is
        spoken for; a second Food Lion swipe finds nothing, because the only
        row named for its merchant is already explained.

        **The envelope holds a SECOND purchase so that its own priced figure
        (`$84.12`) is not the line's.**  With one purchase the envelope row
        and the purchase are both exact candidates at `$54.12`, and the
        tier's assignment takes the ENVELOPE -- leaving the purchase in
        ``unmatched_rows`` and this case measuring which candidate the
        assignment prefers rather than what a claimed row costs.  That
        pricing is ``balance:X-bi-4a``'s (its review's H1) and not this
        step's; the second purchase keeps the case about its own claim under
        either pricing.
        """
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="54.12", description="Food Lion",
        )
        a_purchase(seed_user, groceries, amount="30.00", description="Kobo")
        statement = an_import(seed_user)
        exact = _a_swipe(seed_user, statement, amount="-54.12")
        other = _a_swipe(
            seed_user, statement, amount="-10.89", sequence_in_group=1,
        )

        review = review_set(a_scope(seed_user))

        [proposal] = [
            p for p in review.proposals
            if exact.id in {line.line_id for line in p.lines}
        ]
        assert [row.label for row in proposal.rows] == ["Groceries: Food Lion"]
        assert _the_fact_for(seed_user, other) is None

    def test_a_bill_whose_label_does_NOT_name_the_merchant_is_the_known_limit(
        self, app, db, seed_user,
    ):
        """``Car insurance`` against a GEICO line, no rule: nothing fires.

        Pinned because the ruling states it: such a bill is caught only by
        the near tier's 0.5% bound (**R-GD(b)**), and a period-wide amount-
        blind bill list was the rejected shape that warns on every swipe.
        """
        a_transaction(seed_user, name="Car insurance", amount="100.00")
        line = _a_swipe(
            seed_user, an_import(seed_user), amount="-101.37",
            merchant="GEICO",
        )

        assert _the_fact_for(seed_user, line) is None


class TestTheScreenAndTheDoorReadOneValue:
    """Finding **N-359**'s rule, one direction over."""

    def test_the_withheld_sentence_IS_the_fact_s_own_clause(
        self, app, db, seed_user,
    ):
        """Under a rule, the verdict quotes the value the card carries."""
        groceries = an_envelope(seed_user)
        a_purchase(
            seed_user, groceries, amount="50.00", description="Food Lion",
        )
        a_rule(seed_user, MERCHANT, template_id=groceries.template_id)
        line = _a_swipe(seed_user, an_import(seed_user))

        review = review_set(a_scope(seed_user))
        [item] = [c for c in review.creatable if c.line.line_id == line.id]

        assert item.verdict is not None
        assert item.verdict.withheld == item.already_held.why_it_could_double_count
        assert item.already_held.why_it_could_double_count in item.warning

    def test_a_REFUND_a_container_answer_claims_gets_the_ARRIVING_question(
        self, app, db, seed_user,
    ):
        """The direction guard: a creatable INFLOW is asked the period question.

        Ruling **R-II** routes a merchant credit into the purchase pipeline;
        its double-count fact stays the arriving one (a salary row nothing
        explains), never a spending one.
        """
        groceries = an_envelope(seed_user)
        a_rule(seed_user, MERCHANT, template_id=groceries.template_id)
        # Smaller than the refund: the arriving fact's own proof drops a
        # deposit smaller than the smallest row, and this case is about
        # WHICH question is asked, not that proof.
        a_transaction(
            seed_user, name="Interest", amount="15.00", income=True,
        )
        refund = _a_swipe(seed_user, an_import(seed_user), amount="20.00")

        held = _the_fact_for(seed_user, refund)

        assert isinstance(held, ArrivalsAlreadyHeld)
        assert held.total == Decimal("15.00")
