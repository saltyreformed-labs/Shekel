"""An answered inflow is pre-filled by its rule, and the opened pane is priced from the ask."""

from decimal import Decimal

from app.services.statement_match import OpenedAsk, RowKind, Tab, reconcile_page
from app.services.statement_match._preview import NOTHING_TICKED
from app.services.statement_match._submission import ReviewedDifference

from tests.test_services.test_statement_match._builders import (
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    an_envelope,
    an_unexplained_outflow,
)

from ._helpers import _CARD_PAYMENT, _page, _cards


class TestAnANSWEREDInflowIsPreFilledByItsRule:
    """Ruling **R-HT(a)** meeting **R-HS**, plan step ``bank_import:X-gj-2a``.

    **R-HX set a condition rather than a permanent bar**: every unmatched
    inflow reads *Choose what this is* "until ``X-gj-2`` ships R-HT(a)'s inflow
    rule and the destination becomes one the app can defend".  A stated rule is
    exactly that defence, so a deposit it answers for gets the pre-filled
    sentence R-HS asks for -- and one it does not still asks.
    """

    def _deposit(self, seed_user, merchant="Dividend Earned"):
        """Stage one unexplained deposit from *merchant*."""
        an_unexplained_outflow(
            seed_user, merchant=merchant, amount="0.15",
        )

    def test_a_ruled_deposit_states_what_it_will_be_recorded_as(
        self, app, db, seed_user,
    ):
        """*Add as Income: Salary*, with the verb first (**R-HR**)."""
        an_envelope(seed_user)
        category = seed_user["categories"]["Salary"]
        a_rule(
            seed_user, "Dividend Earned", income_category_id=category.id,
        )
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        said = " ".join(span.text or "" for span in card.sentence)
        assert card.sentence[0].text == "Add"
        assert category.display_name in said
        assert card.offers_ok is True

    def test_a_merchant_CREDIT_is_PRE_FILLED_as_a_REFUND(
        self, app, db, seed_user,
    ):
        """A refund is not income, and the card offers it as the purchase it is.

        The merchant has a SPENDING answer, so ruling **R-HT(a)** makes this
        credit its INVERSE -- a negative purchase back into the same container.
        **This case asserted the card ASKING until plan step
        ``bank_import:X-gj-2b-2``**, because the act did not exist; the card
        said *Choose* and the panel explained why nothing was filed.

        It now takes the PURCHASE verb, which is the visible half of the
        routing this step corrected: what a line becomes is decided by the
        owner's answer and not by the line's sign, so a claimed credit reaches
        the same card an outflow from that merchant reaches.
        """
        envelope = an_envelope(seed_user)
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        self._deposit(seed_user, merchant="Amazon")
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert len(cards) == 1
        card = cards[0]
        # The PURCHASE verb, not the deposit's *Choose what this is*.
        assert card.sentence[0].text == "Add"
        # And the rule names WHERE, so the owner is not asked to pick.
        assert card.suggested is not None

    def test_an_ALWAYS_ASK_deposit_is_not_pre_filled(
        self, app, db, seed_user,
    ):
        """The answer whose whole content is *keep asking me*.

        It names no income category, so it must reach the card exactly as
        having said nothing does -- and it must NOT be reported as a rule this
        pass withheld, which would be a sentence about a decision the owner
        made saying the app failed to act on it.
        """
        an_envelope(seed_user)
        a_rule(seed_user, "Dividend Earned", always_ask=True)
        self._deposit(seed_user)
        db.session.commit()

        cards = _cards(_page(seed_user, Tab.TO_EXPLAIN))

        assert cards[0].sentence[0].text == "Choose"
        assert not any("refund" in note for note in cards[0].panel.notes)


class TestTheOpenedPaneIsPricedFromTheAsk:
    """Plan step ``bank_import:X-gi-2a``, finding **BI-478**, ruling **R-BI3**.

    :func:`~app.services.statement_match.reconcile_page` took the line id
    alone and priced every ``?open=`` pane from the proposal the card offers;
    it takes an :class:`~app.services.statement_match.OpenedAsk` now, and the
    pane is priced from what the ask says the form holds.  **The route tests
    grade the page a browser receives; this grades the SEAM**, so a caller
    handing the page a submission cannot have it silently priced from the
    proposal instead.
    """

    @staticmethod
    def _a_card_payment_and_a_payback(seed_user, db):
        """Stage a parked `-$793.23` payment and one `-$93.23` payback.

        Returns:
            ``(line, payback)``.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        payback = a_transaction(
            seed_user, name="Payback", amount="93.23",
        )
        db.session.commit()
        return line, payback

    def _opened(self, seed_user, ask):
        """Return the Transfers tab's opened pane for *ask*."""
        page = reconcile_page(
            a_scope(seed_user), None, Tab.TRANSFERS, opened=ask,
        )
        assert page.opened is not None, "the card did not open at all"
        return page.opened

    def test_no_form_prices_the_proposal_which_for_a_parked_card_is_nothing(
        self, app, db, seed_user,
    ):
        """``submitted=None`` is the GET: the card as it stands untouched."""
        line, _ = self._a_card_payment_and_a_payback(seed_user, db)

        opened = self._opened(seed_user, OpenedAsk(line_id=line.id))

        assert opened.ticked == frozenset()
        assert opened.consent is None
        assert opened.totals.remedy == NOTHING_TICKED

    def test_a_submission_prices_the_pane_from_ITS_rows(
        self, app, db, seed_user,
    ):
        """The form's own tick is what the pane is priced against."""
        line, payback = self._a_card_payment_and_a_payback(seed_user, db)
        scope = a_scope(seed_user)
        submitted = a_submission(
            scope, lines=(line,), transactions=(payback,),
        )

        opened = self._opened(
            seed_user, OpenedAsk(line_id=line.id, submitted=submitted),
        )

        assert opened.ticked == {(RowKind.TRANSACTION, payback.id)}
        assert opened.totals.bank == Decimal("-793.23")
        assert opened.totals.app == Decimal("-93.23")
        assert opened.totals.difference == Decimal("-700.00")
        assert [option.value for option in opened.totals.options] == [
            "-700.00",
        ], "the lone row's one act was not offered against the submitted rows"

    def test_the_consent_is_carried_whole_and_never_read_by_the_preview(
        self, app, db, seed_user,
    ):
        """Ruling **R-BI3**'s seam: the echo is the request's, the options
        are the arithmetic's.

        A consent to a figure the rows no longer come to is still CARRIED --
        the template compares it against each option and picks none -- and
        it changes nothing about which acts are offered.
        """
        line, payback = self._a_card_payment_and_a_payback(seed_user, db)
        scope = a_scope(seed_user)
        stale = a_submission(
            scope, lines=(line,), transactions=(payback,), residual="-50.00",
        )

        opened = self._opened(
            seed_user, OpenedAsk(line_id=line.id, submitted=stale),
        )

        assert opened.consent == ReviewedDifference(figure=Decimal("-50.00"))
        assert [option.value for option in opened.totals.options] == [
            "-700.00",
        ]
        assert all(
            option.accepts != opened.consent for option in opened.totals.options
        ), "an option equals a consent to a figure the rows do not come to"
