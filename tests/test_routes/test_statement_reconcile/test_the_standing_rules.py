"""What a standing rule does from this page: a rule naming a one-off envelope places its row, the
receipt offers one rule per merchant from what the door applied, and a Never answer returns its
line to the inbox.
"""

from datetime import date, timedelta
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.models.category import Category
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.statement_match import place_token
from tests.test_routes._statement_forms import form_fields, reconcile_form_fields
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_one_off_envelope,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    the_merchant_id,
)

from ._helpers import _url, _page, _choosing, _post, _a_swipe_a_rule_files


class TestARuleNamingAOneOffEnvelopePlacesItsRowFromThisPage:
    """Leaf 7b-3 of balance:X-bi-7b (ruling R-BAL24): the PLACE option, end to end.

    A standing TEMPLATE answer names a one-off envelope's DEFINITION, and the
    line falls in a paycheck holding no row of it.  The card offers ONE
    option for that -- the placement's own token -- and OK then Apply places
    the definition's row there and records the purchase into it.  Kept in
    its own class: ``bank_import:X-gz`` lands its cases in this package too,
    in ``test_the_match_pane.py``.
    """

    def test_the_card_offers_the_place_option_and_apply_places_the_row(
        self, auth_client, db, seed_user,
    ):
        """Rendered selected, posted as rendered, placed by the door."""
        existing = a_one_off_envelope(seed_user, name="Amazon")
        definition_id = existing.template_id
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-22.10",
            posted_on=later.start_date + timedelta(days=2),
            description="POINT OF SALE DEBIT L340 (Amazon)", merchant="Amazon",
        )
        db.session.commit()
        a_rule(seed_user, "Amazon", template_id=definition_id)
        db.session.commit()

        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)
        token = place_token(definition_id)
        assert (f"destination-{line.id}", token) in fields, (
            "the card did not render the PLACE option selected for this line"
        )
        assert "placed in this paycheck" in page

        response = _post(
            auth_client, seed_user, fields + [("ok", str(line.id))], page,
        )

        assert response.status_code == 200
        rows = (
            db.session.query(Transaction)
            .filter_by(template_id=definition_id, is_deleted=False)
            .order_by(Transaction.id).all()
        )
        assert [row.id for row in rows][0] == existing.id and len(rows) == 2
        placed = rows[1]
        assert placed.pay_period_id == later.id
        entry = db.session.query(TransactionEntry).one()
        assert entry.transaction_id == placed.id
        assert entry.amount == Decimal("22.10")


class TestTheReceiptOffersOneStandingRulePerMerchant:
    """Ruling **bank_import:R-IB** (developer, 2026-08-30).

    The card carried an *always, for this merchant* checkbox until that
    ruling. A standing rule is ONE fact per merchant and a card is one LINE,
    so on the developer's own pass the page asked one question **86 times** --
    Amazon 26, Walmart 13, Food Lion 12. The offer is on the RECEIPT now,
    once per merchant, about what the door actually APPLIED.
    """

    @staticmethod
    def _ok_a_swipe(auth_client, seed_user, line, envelope):
        """OK one card into *envelope* and return the answering body."""
        page = _page(auth_client, seed_user)
        chosen = _choosing(
            reconcile_form_fields(page),
            f"destination-{line.id}", str(envelope.id),
        )
        response = _post(
            auth_client, seed_user, chosen + [("ok", str(line.id))], page,
        )
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_the_receipt_offers_the_rule_the_pass_actually_filed(
        self, auth_client, db, seed_user,
    ):
        """The offer names the merchant, the count and where it went."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)

        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)

        assert "Should any of these stand?" in body
        assert "Always file Lowe&#39;s in" in body
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' in body

    def test_pressing_it_records_the_rule(
        self, auth_client, db, seed_user,
    ):
        """The offer's own bytes go back to the shipped rule door."""
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        chosen = _choosing(
            offered, f"rule-{merchant_id}", f"t:{envelope.template_id}",
        )
        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + chosen),
        )

        assert response.status_code == 200
        rule = db.session.query(MerchantRule).filter_by(
            merchant_id=merchant_id,
        ).one()
        assert rule.template_id == envelope.template_id

    def test_the_offer_OPENS_on_not_now_and_writes_nothing_unpressed(
        self, auth_client, db, seed_user,
    ):
        """Nothing is pre-selected: the owner has not said it yet.

        The destination they picked is a fact about THIS purchase; whether it
        should stand is a different question about the future, and a radio
        group arriving on the answer would make Save record something nobody
        chose.
        """
        envelope, line = _a_swipe_a_rule_files(seed_user, db)
        merchant_id = the_merchant_id(seed_user, "Lowe's")
        body = self._ok_a_swipe(auth_client, seed_user, line, envelope)
        db.session.query(MerchantRule).delete()
        db.session.commit()

        offered = form_fields(body, "/reconcile/merchants")
        assert (f"rule-{merchant_id}", "unset") in offered, (
            "the offer arrived pre-selected, so Save would record an answer "
            "the owner never gave"
        )

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/reconcile/merchants",
            data=MultiDict([("csrf_token", "x")] + offered),
        )

        assert response.status_code == 200
        assert db.session.query(MerchantRule).count() == 0

    def test_ONE_offer_however_many_lines_that_merchant_has(
        self, auth_client, db, seed_user,
    ):
        """The grain the ruling corrects, at the smallest size that shows it.

        Two Lowe's swipes filed into the same envelope are ONE answer, not
        two: a rule keys on ``template_id``, so every pay period's copy of an
        envelope is the same rule. On the developer's own data this is why the
        contradiction rate is zero across 10 repeated merchants.
        """
        envelope, first = _a_swipe_a_rule_files(seed_user, db)
        second = an_unexplained_outflow(
            seed_user, merchant="Lowe's", amount="-12.10",
        )
        db.session.commit()
        merchant_id = the_merchant_id(seed_user, "Lowe's")

        page = _page(auth_client, seed_user)
        fields = reconcile_form_fields(page)
        for line in (first, second):
            fields = _choosing(
                fields, f"destination-{line.id}", str(envelope.id),
            )
        response = _post(auth_client, seed_user, fields + [
            ("ok", str(first.id)), ("ok", str(second.id)),
        ], page)

        body = response.get_data(as_text=True)
        assert body.count(f'name="rule_merchant-{merchant_id}"') == 1, (
            "one merchant was asked about more than once"
        )
        assert "You filed 2 <strong>Lowe&#39;s</strong>" in body


class TestTheROUTEHandsTheOfferWhatTheDoorAPPLIED:
    """The SEAM, graded where the two derivations differ.

    ``rules_worth_offering`` takes the APPLIED items as a PARAMETER (the
    id set ``applied_line_ids`` until plan step ``bank_import:X-gx``, the
    items themselves since), so a service test can only grade what a caller
    passes -- and an adversarial review proved the gap by hand: replacing the
    route's

        frozenset(l for item in outcome.applied for l in item.line_ids)

    with the pre-ruling ``frozenset(item["line_id"] for item in creations)``
    left **11,969 tests green**, shipping the exact regression ruling
    **bank_import:R-IB** exists to make unconstructible.  The route hands
    ``outcome.applied`` now, and the equivalent regression is an item built
    from the submission rather than from the door.

    **Choosing the refusal is the whole difficulty**, because
    ``rules_worth_offering`` has more than one way to drop a line and only
    one of them is the seam. A destination the pass does not offer is dropped
    by its own arm, and since plan step ``bank_import:X-gx`` a line no applied
    item names a merchant for -- not applied, or merchant-less -- is dropped
    by the other (a line that was not ``creatable`` had an arm of its own
    until then; an answer the rule door would refuse is not dropped but
    carried as ``blocked``). The refusal used here trips none of them: a line
    past the saved pay calendar
    IS creatable, is filed to a NEW envelope under an ACTIVE category -- an
    answer the rule door takes -- and the create door refuses it by name at
    ``scope.period_holding``. So the only thing that can keep it out of the
    offer is the narrowing this class is about.
    """

    def test_a_line_the_door_REFUSED_is_not_in_the_receipts_offer(
        self, auth_client, db, seed_user,
    ):
        """One merchant lands, one is refused; only the lander is offered."""
        envelope, lands = _a_swipe_a_rule_files(seed_user, db)
        db.session.query(MerchantRule).delete()
        far = a_bank_line(
            seed_user, an_import(seed_user), amount="-40.00",
            posted_on=date(2031, 3, 4), merchant="Faraway Co",
        )
        db.session.commit()
        category = db.session.query(Category).filter(
            Category.user_id == seed_user["user"].id,
            Category.is_active.is_(True),
        ).first()

        # **Posted directly and not through ``_post``**, and deliberately: the
        # far line's ADD tab is SHUT (its ``withheld`` names the missing pay
        # period), so no card renders a destination for it and no browser
        # could send this. A stale page is how it arises -- the calendar is
        # extended, the page is drawn, the schedule is rolled back -- and the
        # per-item SAVEPOINT is what this is about either way.
        response = auth_client.post(
            _url(seed_user["account"].id),
            data=MultiDict([
                ("csrf_token", "x"),
                ("ok", str(lands.id)),
                (f"verb-{lands.id}", "add"),
                (f"destination-{lands.id}", str(envelope.id)),
                ("ok", str(far.id)),
                (f"verb-{far.id}", "add"),
                (f"destination-{far.id}", "new"),
                (f"envelope_name-{far.id}", "Faraway"),
                (f"category_id-{far.id}", str(category.id)),
            ]),
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "No pay period covers 2031-03-04" in body, (
            "the door did not refuse the far line, so this grades nothing"
        )
        assert f'name="rule_merchant-{the_merchant_id(seed_user, "Lowe\'s")}"' \
            in body, "the merchant that LANDED earned no offer"
        assert 'name="rule_merchant-' + str(
            the_merchant_id(seed_user, "Faraway Co"),
        ) + '"' not in body, (
            "a purchase the door REFUSED earned a standing-rule offer -- the "
            "next import would auto-file that merchant with no press"
        )


class TestANeverAnswerReturnsItsLineToTheInbox:
    """Ruling **bank_import:R-JH**, plan step ``bank_import:X-gj-4c-1``.

    **Read off the markup a browser would receive**, because what this step
    moved is where a line RENDERS and the service test one tier down cannot
    see a tab bar, an Apply form or a link.  A standing *never a purchase*
    answer shuts the ADD door and claims nothing about what the line is, so
    the line is inbox work rather than a disposition.

    **The staging is deliberately an ORDINARY swipe merchant.**  A merchant a
    source ALSO files as paying an account the owner holds is a transfer
    whatever they answered, stays parked, and is graded by
    ``test_the_doors.py``'s :class:`TestAVerbWithNoDoorRendersNoControl`.
    """

    @staticmethod
    def _a_never_answered_swipe(seed_user, db):
        """Stage one outflow whose merchant the owner answered NEVER for.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.

        Returns:
            The staged bank line.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Foundation Donation", amount="-4.00",
        )
        a_rule(seed_user, "Foundation Donation")
        db.session.commit()
        return line

    def test_the_card_is_on_TO_EXPLAIN_under_nothing_suggested(
        self, auth_client, db, seed_user,
    ):
        """The tab bar counts it as work, and the section heading says so."""
        self._a_never_answered_swipe(seed_user, db)

        page = _page(auth_client, seed_user)

        assert "Foundation Donation" in page
        assert "Nothing suggested" in page

    def test_it_is_on_NEITHER_holding_tab(self, auth_client, db, seed_user):
        """Both halves, and the TRANSFERS half is the one that renders money.

        Leaving the line in ``parked`` and merely dropping the tab arm would
        total that list on TRANSFERS, whose holding chip carries a COUNT and a
        MAGNITUDE -- so the line would arrive under a rendered money figure the
        bank never filed as a payment.
        """
        self._a_never_answered_swipe(seed_user, db)

        transfers = _page(auth_client, seed_user, "transfers")
        skipped = _page(auth_client, seed_user, "skipped")

        assert "Foundation Donation" not in transfers
        assert "Foundation Donation" not in skipped
        assert "waiting for the account they paid" not in transfers

    def test_ADD_renders_the_owner_s_own_reason_and_NO_destination_chooser(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GJ**'s bar, one tab over from where it used to render.

        **The chooser is what this asserts the absence of**, not the sentence:
        a warning paragraph over a working select is the shape that ruling cost
        `$7,412.94` to learn, and putting these lines back in the inbox is
        exactly the change that could have reintroduced it.
        """
        line = self._a_never_answered_swipe(seed_user, db)

        page = _page(auth_client, seed_user)

        assert "You have said Foundation Donation is never a purchase" in page
        assert f'name="destination-{line.id}"' not in page, (
            "a barred line must render no destination chooser, on any tab"
        )

    def test_it_keeps_the_CONSENT_and_the_door_that_changes_the_answer(
        self, auth_client, db, seed_user,
    ):
        """**Why R-HQ is not breached by putting this line in the inbox.**

        MATCH is open, so the card has an act and the ``ok`` checkbox has to be
        in the document for it -- the same reason a parked card payment keeps
        one.  And unlike that line this one has an answer worth changing, so
        the panel names the merchants page.
        """
        line = self._a_never_answered_swipe(seed_user, db)
        a_transaction(seed_user, name="Groceries", is_envelope=True)
        db.session.commit()

        page = _page(auth_client, seed_user)

        assert f'name="ok" value="{line.id}"' in page, (
            "a line whose only shut door is ADD must still be matchable"
        )
        assert "Change what you have said about Foundation Donation" in page
