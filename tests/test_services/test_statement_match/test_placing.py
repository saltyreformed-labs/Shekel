"""
Shekel Budget App -- a merchant answer NAMES a definition (leaf 7b-3 of balance:X-bi-7b)

``bank_import:X-f6c``'s shape, ruling **R-BAL24**, finding **N-328**: a
NEW-ENVELOPE merchant answer mints ONE rule-less definition the first time it
fires and names it thereafter, and a TEMPLATE answer naming a rule-less
definition PLACES a row of it in any paycheck that holds none.  Graded end to
end through the doors the app has -- the filing door an import runs
(``file_new_swipes``), the review set the Reconcile page derives
(``review_set``), the create door and the schema field -- so what is
measured is the row the app writes and the rule it rewrites.

The two 7b-3 forks the developer ruled 2026-09-16 are graded beside their
own doors: R-BAL43 in ``tests/test_routes/test_one_off_row_doors.py`` and
``test_one_off.py``, R-BAL44 in
``test_carry_forward_service/test_a_leftover_row_is_dated.py``.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from marshmallow import ValidationError as MarshmallowValidationError

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.schemas.validation.statements import PurchaseDestination
from app.services import status_seam
from app.services.statement_match import (
    NEW_ENVELOPE,
    MintedEnvelopes,
    PlaceIn,
    PlacementKind,
    PurchaseCreation,
    RuleView,
    create_purchase_from_line,
    file_new_swipes,
    parse_place_token,
    place_token,
    review_set,
)
# pylint: disable-next=shekel-private-module-import
from app.services.statement_match._placement import placements_for
# pylint: disable-next=shekel-private-module-import
from app.services.statement_match._sentence import for_placement
from tests._test_helpers import resolved_amount

from ._builders import (
    a_bank_line,
    a_later_period,
    a_one_off_envelope,
    a_rule,
    a_scope,
    an_answers,
    an_envelope,
    an_import,
    the_merchant_id,
)


def _swipe(seed_user, statement, *, day, amount="-31.56", merchant="Amazon"):
    """Stage one recorded card swipe under *statement* on *day*."""
    return a_bank_line(
        seed_user, statement, amount=amount, posted_on=day,
        description=f"POINT OF SALE DEBIT L340 ({merchant}) {day}",
        merchant=merchant,
    )


def _rows_of(template_id):
    """Return the live rows of *template_id*, oldest first."""
    return (
        db.session.query(Transaction)
        .filter_by(template_id=template_id, is_deleted=False)
        .order_by(Transaction.id)
        .all()
    )


class TestANewEnvelopeAnswerMintsOnceAndNamesTheDefinition:
    """N-328's ruling, on the filing door an import runs."""

    def test_the_first_firing_mints_a_definition_and_rewrites_the_rule(
        self, app, db, seed_user,
    ):
        """One press, two paychecks: ONE definition, one placed row per paycheck.

        Before this leaf the second paycheck's line minted a second link-less
        envelope of the same name; the answer now converges on the definition
        its first line minted (``MintedEnvelopes``), and the stored answer
        becomes TEMPLATE naming it.
        """
        with app.app_context():
            statement = an_import(seed_user)
            category = seed_user["categories"]["Groceries"]
            first_day = seed_user["bootstrap_period"].start_date
            later_day = a_later_period(seed_user).start_date
            _swipe(seed_user, statement, day=first_day)
            _swipe(seed_user, statement, day=later_day, amount="-12.00")
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=category.id,
            )
            db.session.flush()

            filing = file_new_swipes(a_scope(seed_user), statement.id)
            db.session.flush()

            assert filing.outcome.envelopes_created == 2
            definitions = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Amazon").all()
            )
            assert len(definitions) == 1
            definition = definitions[0]
            assert definition.recurs is False and definition.is_envelope is True
            rows = _rows_of(definition.id)
            assert [row.pay_period_id for row in rows] == sorted(
                {row.pay_period_id for row in rows},
            ) and len(rows) == 2
            assert [len(row.entries) for row in rows] == [1, 1]
            stored = db.session.query(MerchantRule).one()
            assert stored.template_id == definition.id
            assert stored.envelope_name is None and stored.category_id is None

    def test_the_next_statement_PLACES_a_row_where_the_paycheck_holds_none(
        self, app, db, seed_user,
    ):
        """The rewritten answer reaches a later paycheck with no row of its own.

        Statement one mints the definition; statement two's line falls in a
        paycheck holding no row of it, and the TEMPLATE answer places one
        there rather than resolving UNRESOLVED (10.6-D's regression, closed).
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            first = an_import(seed_user)
            _swipe(seed_user, first, day=seed_user["bootstrap_period"].start_date)
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=category.id,
            )
            db.session.flush()
            file_new_swipes(a_scope(seed_user), first.id)
            db.session.commit()
            definition = db.session.query(TransactionTemplate).filter_by(
                name="Amazon",
            ).one()

            second = an_import(seed_user)
            later_day = a_later_period(seed_user).start_date + timedelta(days=2)
            _swipe(seed_user, second, day=later_day, amount="-8.40")
            db.session.flush()
            review = review_set(a_scope(seed_user))
            placement = next(
                line.placement for line in review.creatable
                if line.line.posted_on == later_day
            )
            assert placement.kind is PlacementKind.PLACE
            assert placement.placed.template_id == definition.id
            assert placement.select_value == place_token(definition.id)

            filing = file_new_swipes(a_scope(seed_user), second.id)
            db.session.flush()
            assert filing.outcome.envelopes_created == 1
            rows = _rows_of(definition.id)
            assert len(rows) == 2
            placed = rows[1]
            assert placed.is_placed is True
            assert placed.due_date == placed.occurs_on
            assert [entry.amount for entry in placed.entries] == [Decimal("8.40")]
            assert placed.status.is_settled is True

    def test_a_first_firing_converges_on_a_placed_envelope_of_that_name(
        self, app, db, seed_user,
    ):
        """The owner's grid one-off "Amazon" takes the line, and the rule names ITS definition."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            existing = a_one_off_envelope(
                seed_user, name="Amazon", category=category,
            )
            statement = an_import(seed_user)
            _swipe(seed_user, statement, day=seed_user["bootstrap_period"].start_date)
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=category.id,
            )
            db.session.flush()

            filing = file_new_swipes(a_scope(seed_user), statement.id)
            db.session.flush()

            assert filing.outcome.envelopes_created == 0
            db.session.refresh(existing)
            assert [entry.amount for entry in existing.entries] == [Decimal("31.56")]
            stored = db.session.query(MerchantRule).one()
            assert stored.template_id == existing.template_id
            assert stored.envelope_name is None

    def test_a_converged_first_firing_and_a_later_paycheck_share_one_definition(
        self, app, db, seed_user,
    ):
        """Found by 7b-3's adversarial review: the registry learns a CONVERGENCE too.

        Line one falls where the owner's grid "Amazon" already sits and
        converges on it; line two falls in a later paycheck.  A first build
        remembered only what the press MINTED, so line two minted a second
        "Amazon" definition beside the owner's.  One definition, two rows.
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            existing = a_one_off_envelope(
                seed_user, name="Amazon", category=category,
            )
            statement = an_import(seed_user)
            _swipe(seed_user, statement, day=seed_user["bootstrap_period"].start_date)
            _swipe(
                seed_user, statement, day=a_later_period(seed_user).start_date,
                amount="-12.00",
            )
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=category.id,
            )
            db.session.flush()

            filing = file_new_swipes(a_scope(seed_user), statement.id)
            db.session.flush()

            assert filing.outcome.envelopes_created == 1
            assert db.session.query(TransactionTemplate).filter_by(
                name="Amazon",
            ).count() == 1
            rows = _rows_of(existing.template_id)
            assert len(rows) == 2 and rows[0].id == existing.id
            assert [len(row.entries) for row in rows] == [1, 1]
            assert db.session.query(MerchantRule).one().template_id == (
                existing.template_id
            )

    def test_an_owners_pick_of_a_recurring_envelope_of_that_name_rewrites_no_rule(
        self, app, db, seed_user,
    ):
        """THE CONTROL on the flip's key (found by 7b-3's adversarial review).

        The stored answer is NEW-ENVELOPE "Groceries"; the owner files the
        line into their RECURRING Groceries envelope by hand.  A first build
        keyed the flip on the link alone and turned the answer into TEMPLATE
        naming the recurring definition -- a standing answer the owner never
        stated.  The rule stays as stated.
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            recurring = an_envelope(seed_user, name="Groceries")
            assert recurring.template_id is not None and recurring.recurs
            statement = an_import(seed_user)
            line = _swipe(
                seed_user, statement, day=seed_user["bootstrap_period"].start_date,
            )
            a_rule(
                seed_user, "Amazon", envelope_name="Groceries",
                category_id=category.id,
            )
            db.session.flush()

            create_purchase_from_line(
                PurchaseCreation(line_id=line.id, transaction_id=recurring.id),
                a_scope(seed_user), MintedEnvelopes.none_yet(), an_answers(seed_user),
                applied_by_rule=False,
            )
            db.session.flush()

            stored = db.session.query(MerchantRule).one()
            assert stored.template_id is None
            assert stored.envelope_name == "Groceries"

    def test_an_owners_own_new_envelope_rewrites_no_rule(
        self, app, db, seed_user,
    ):
        """THE CONTROL: a one-line choice that is not the rule's answer leaves the rule alone."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            statement = an_import(seed_user)
            line = _swipe(
                seed_user, statement, day=seed_user["bootstrap_period"].start_date,
            )
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon Household",
                category_id=category.id,
            )
            db.session.flush()

            from app.services.statement_match import NewEnvelope  # pylint: disable=import-outside-toplevel
            create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id,
                    new_envelope=NewEnvelope(name="Gifts", category_id=category.id),
                ),
                a_scope(seed_user), MintedEnvelopes.none_yet(), an_answers(seed_user),
                applied_by_rule=False,
            )
            db.session.flush()

            stored = db.session.query(MerchantRule).one()
            assert stored.template_id is None
            assert stored.envelope_name == "Amazon Household"


class TestThePlaceArmAtTheCreateDoor:
    """``resolve_destination``'s third arm and its refusals."""

    def test_a_place_creation_places_the_definitions_row_in_the_lines_paycheck(
        self, app, db, seed_user,
    ):
        """Dated at the paycheck's start, answering it, priced by the definition."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            definition_id = existing.template_id
            statement = an_import(seed_user)
            later = a_later_period(seed_user)
            line = _swipe(
                seed_user, statement, day=later.start_date + timedelta(days=3),
            )
            db.session.flush()

            recorded = create_purchase_from_line(
                PurchaseCreation(line_id=line.id, template_id=definition_id),
                a_scope(seed_user), MintedEnvelopes.none_yet(), an_answers(seed_user),
                applied_by_rule=False,
            )
            db.session.flush()

            assert recorded.envelope_created is True
            assert recorded.template_id == definition_id
            placed = db.session.get(Transaction, recorded.transaction_id)
            assert placed.template_id == definition_id
            assert placed.pay_period_id == later.id
            assert placed.due_date == later.start_date
            assert placed.occurs_on == later.start_date
            assert placed.estimated_amount is None
            assert resolved_amount(placed) == Decimal("0.00")

    def test_two_lines_in_one_paycheck_share_the_placed_row(
        self, app, db, seed_user,
    ):
        """The registry's row key: one press places one row per definition per paycheck."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            definition_id = existing.template_id
            statement = an_import(seed_user)
            later = a_later_period(seed_user)
            first = _swipe(seed_user, statement, day=later.start_date)
            second = _swipe(
                seed_user, statement, day=later.start_date + timedelta(days=1),
                amount="-4.00",
            )
            db.session.flush()
            minted = MintedEnvelopes.none_yet()
            scope, answers = a_scope(seed_user), an_answers(seed_user)

            one = create_purchase_from_line(
                PurchaseCreation(line_id=first.id, template_id=definition_id),
                scope, minted, answers, applied_by_rule=False,
            )
            minted.remember(
                PurchaseCreation(line_id=first.id, template_id=definition_id), one,
            )
            two = create_purchase_from_line(
                PurchaseCreation(line_id=second.id, template_id=definition_id),
                scope, minted, answers, applied_by_rule=False,
            )
            db.session.flush()

            assert one.envelope_created is True
            assert two.envelope_created is False
            assert two.transaction_id == one.transaction_id
            assert len(_rows_of(definition_id)) == 2

    def test_a_paycheck_already_holding_a_row_of_it_refuses_a_second(
        self, app, db, seed_user,
    ):
        """The crafted request behind the view's ``placed_periods`` read.

        The screen never offers PLACE where the paycheck holds a row of the
        definition; a submission naming it anyway meets the door's own
        refusal (``one_off.holds_a_row_in``), not the occurrence index.
        """
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            statement = an_import(seed_user)
            line = _swipe(
                seed_user, statement, day=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            with pytest.raises(ValidationError, match="already holds a row"):
                create_purchase_from_line(
                    PurchaseCreation(
                        line_id=line.id, template_id=existing.template_id,
                    ),
                    a_scope(seed_user), MintedEnvelopes.none_yet(),
                    an_answers(seed_user), applied_by_rule=False,
                )

    @pytest.mark.parametrize(
        "shape", ["recurring", "foreign", "plain", "archived", "unknown"],
    )
    def test_a_definition_a_row_may_not_be_placed_for_is_refused(
        self, app, db, seed_user, seed_second_user, shape,
    ):
        """Only a rule-less ENVELOPE definition of THIS account's places a row.

        The FOREIGN arm is another owner's real one-off envelope (a first
        build spelled it as an id that named nothing, which grades the
        lookup and not the ownership; found by 7b-3's adversarial review),
        and the ARCHIVED arm is this owner's own, deactivated.
        """
        from tests._test_helpers import make_expense_template  # pylint: disable=import-outside-toplevel

        with app.app_context():
            if shape == "recurring":
                template = make_expense_template(
                    db.session, seed_user, amount="50.00", name="Streaming",
                    category_key="Groceries", is_envelope=True,
                )
                template_id = template.id
            elif shape == "plain":
                template = make_expense_template(
                    db.session, seed_user, amount="50.00", name="Rent",
                    category_key="Groceries",
                )
                template.recurrence_rule = None
                template_id = template.id
            elif shape == "foreign":
                template_id = a_one_off_envelope(
                    seed_second_user, name="Amazon",
                ).template_id
            elif shape == "archived":
                archived = a_one_off_envelope(seed_user, name="Amazon")
                archived.template.is_active = False
                template_id = archived.template_id
            else:
                template_id = 999_999
            db.session.commit()
            statement = an_import(seed_user)
            line = _swipe(
                seed_user, statement, day=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            with pytest.raises(ValidationError, match="not a one-off envelope"):
                create_purchase_from_line(
                    PurchaseCreation(line_id=line.id, template_id=template_id),
                    a_scope(seed_user), MintedEnvelopes.none_yet(),
                    an_answers(seed_user), applied_by_rule=False,
                )

    def test_two_arms_named_at_once_are_refused(self, app, db, seed_user):
        """Exactly one of three, still."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            statement = an_import(seed_user)
            line = _swipe(
                seed_user, statement, day=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()
            with pytest.raises(ValidationError, match="exactly one place"):
                create_purchase_from_line(
                    PurchaseCreation(
                        line_id=line.id, transaction_id=existing.id,
                        template_id=existing.template_id,
                    ),
                    a_scope(seed_user), MintedEnvelopes.none_yet(),
                    an_answers(seed_user), applied_by_rule=False,
                )


class TestTheTemplatePlacement:
    """``_template_placement``: PLACE for a rule-less definition, UNRESOLVED for a recurring one."""

    def test_a_rule_less_definition_with_no_row_here_is_PLACED(
        self, app, db, seed_user,
    ):
        """And the placement's value, creation and sentence all name the definition."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            a_rule(seed_user, "Amazon", template_id=existing.template_id)
            db.session.commit()
            merchant_id = the_merchant_id(seed_user, "Amazon")
            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)
            assert existing.template_id in view.placeable_templates

            placement = placements_for(
                merchant_id, view, [], period_id=a_later_period(seed_user).id,
            )

            assert placement.kind is PlacementKind.PLACE
            assert placement.places and placement.names_a_home
            assert placement.sweep_class == "creates"
            assert placement.select_value == place_token(existing.template_id)
            creation = placement.creation_for(42)
            assert creation == PurchaseCreation(
                line_id=42, template_id=existing.template_id,
            )
            words = " ".join(
                span.text for span in for_placement(placement) if span.text
            )
            assert "Amazon" in words and "placed in this paycheck" in words

    def test_a_recurring_definition_with_no_row_here_stays_UNRESOLVED(
        self, app, db, seed_user,
    ):
        """THE CONTROL: the engine's rows are the engine's to place."""
        from tests._test_helpers import make_expense_template  # pylint: disable=import-outside-toplevel

        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="50.00", name="Streaming",
                category_key="Groceries", is_envelope=True,
            )
            a_rule(seed_user, "Amazon", template_id=template.id)
            db.session.commit()
            merchant_id = the_merchant_id(seed_user, "Amazon")
            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)
            assert template.id not in view.placeable_templates

            placement = placements_for(
                merchant_id, view, [], period_id=a_later_period(seed_user).id,
            )

            assert placement.kind is PlacementKind.UNRESOLVED
            assert "may not have been generated here" in placement.unresolved_reason


    def test_a_paycheck_holding_a_cancelled_row_of_it_is_not_PLACED_again(
        self, app, db, seed_user,
    ):
        """No row AT ALL, not no offerable row (found by 7b-3's adversarial reviews).

        A cancelled row takes no purchase, so it is not offered -- but it is
        still the paycheck's one row of the definition, and a second placed
        beside it met the occurrence index.  The view's ``placed_periods``
        is what the arm reads.
        """
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            a_rule(seed_user, "Amazon", template_id=existing.template_id)
            status_seam.apply_status_change(
                existing, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            merchant_id = the_merchant_id(seed_user, "Amazon")
            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)
            assert view.placed_periods[existing.template_id] == frozenset(
                {existing.pay_period_id},
            )

            placement = placements_for(
                merchant_id, view, [], period_id=existing.pay_period_id,
            )

            assert placement.kind is PlacementKind.UNRESOLVED
            assert "closed at a fixed figure" in placement.unresolved_reason

    def test_a_line_with_no_paycheck_places_nothing(self, app, db, seed_user):
        """A day before the books opened has no paycheck to place a row in."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            a_rule(seed_user, "Amazon", template_id=existing.template_id)
            db.session.commit()
            merchant_id = the_merchant_id(seed_user, "Amazon")
            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)

            placement = placements_for(merchant_id, view, [], period_id=None)

            assert placement.kind is PlacementKind.UNRESOLVED


class TestTheScreenSaysWhichLinePlacesTheRow:
    """N-327's sentence rule, on the PLACE arm: a second line in one paycheck JOINS."""

    def test_the_second_line_in_one_paycheck_joins_the_row_the_first_places(
        self, app, db, seed_user,
    ):
        """Two lines, one paycheck, one placed row: the card says so before the press."""
        with app.app_context():
            existing = a_one_off_envelope(seed_user, name="Amazon")
            a_rule(seed_user, "Amazon", template_id=existing.template_id)
            statement = an_import(seed_user)
            later = a_later_period(seed_user)
            _swipe(seed_user, statement, day=later.start_date)
            _swipe(seed_user, statement, day=later.start_date + timedelta(days=1), amount="-4.00")
            db.session.commit()

            review = review_set(a_scope(seed_user))
            placing = [
                line.placement for line in review.creatable
                if line.placement is not None and line.placement.places
            ]
            assert [placement.joins_new for placement in placing] == [False, True]
            words = " ".join(
                span.text for span in for_placement(placing[1]) if span.text
            )
            assert "joining the one this pass places" in words


class TestThePlaceTokenOnTheWire:
    """The destination field reads what the placement writes, and nothing else."""

    def test_the_token_round_trips_through_the_schema_field(self):
        """One spelling, both directions."""
        assert parse_place_token(place_token(17)) == 17
        assert PurchaseDestination().deserialize(place_token(17)) == PlaceIn(
            template_id=17,
        )
        assert PurchaseDestination().deserialize(NEW_ENVELOPE) == NEW_ENVELOPE
        assert PurchaseDestination().deserialize("23") == 23

    @pytest.mark.parametrize("value", ["place:", "place:abc", "place:0", "place:-3", "placed:7"])
    def test_a_malformed_token_is_refused(self, value):
        """As strict as a row id, and no other prefix passes."""
        assert parse_place_token(value) is None
        with pytest.raises(MarshmallowValidationError):
            PurchaseDestination().deserialize(value)


class TestTheRegistry:
    """``MintedEnvelopes``: keyed on the definition."""

    def test_an_answer_maps_to_its_definition_and_a_definition_to_its_row_per_paycheck(self):
        """Both keys, written by remember, read by the two lookups."""
        from app.services.statement_match import CreatedPurchase, NewEnvelope  # pylint: disable=import-outside-toplevel
        from datetime import date  # pylint: disable=import-outside-toplevel

        minted = MintedEnvelopes.none_yet()
        answer = NewEnvelope(name="Amazon", category_id=4)
        assert minted.definition_for(answer) is None
        created = CreatedPurchase(
            entry_id=1, transaction_id=10, match_id=100, envelope_label="Amazon",
            envelope_created=True, amount=Decimal("5.00"), posts_on=date(2026, 9, 4),
            made_on=date(2026, 9, 4), pay_period_id=7, records_a_refund=False,
            merchant=None, template_id=55,
        )
        minted.remember(
            PurchaseCreation(line_id=1, new_envelope=answer), created,
        )
        assert minted.definition_for(answer) == 55
        assert minted.row_for(55, 7) == 10
        assert minted.row_for(55, 8) is None
        # A PLACE creation remembers the row alone; there is no answer to map.
        minted.remember(
            PurchaseCreation(line_id=2, template_id=55),
            CreatedPurchase(
                entry_id=2, transaction_id=11, match_id=101, envelope_label="Amazon",
                envelope_created=True, amount=Decimal("5.00"),
                posts_on=date(2026, 9, 18), made_on=date(2026, 9, 18),
                pay_period_id=8, records_a_refund=False, merchant=None,
                template_id=55,
            ),
        )
        assert minted.row_for(55, 8) == 11
        assert minted.definition_for(NewEnvelope(name="Amazon", category_id=5)) is None

